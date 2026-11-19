# Worker do Telegram

Imagine que você pediu uma massagem para amanhã. O CAPY ofereceu **1. 14h** e **2. 16h**. Quando você responde **“1”**, o worker precisa lembrar qual horário ofereceu, saber de quem é o pedido e preparar a resposta. Acompanhe esse “1” desde a chegada até o envio da confirmação.

Um **worker** é um processo que pega trabalho em segundo plano. O `telegram-worker` cuida da conversa. Outros processos recebem a mensagem, passam avisos pela fila e enviam a resposta. Assim, a API pode confirmar o recebimento enquanto o worker ainda está processando um áudio ou consultando os horários.

## O caminho da mensagem

| Etapa | O que acontece com “1” |
| --- | --- |
| **Receber** | O Telegram chama o webhook do CAPY. A API guarda “1” no PostgreSQL. O relay manda ao RabbitMQ um aviso para consultar aquele chat. |
| **Processar** | O worker pega “1” no PostgreSQL e recupera do Redis os horários oferecidos. O grafo associa a escolha às 14h, e o serviço de agendamento revalida e tenta reservar o horário. O worker salva a resposta no PostgreSQL. |
| **Enviar** | O relay avisa o sender de que há uma resposta pronta. O sender lê a resposta salva e chama a API do Telegram. |

Um **turno** é o processamento de uma mensagem do cliente. Pedir horários e responder “1” são dois turnos do mesmo chat. O **checkpoint** é o estado salvo da conversa, incluindo a lista de horários que liga os dois turnos. A reserva em si fica no PostgreSQL. [Grafo da conversa](graph.md) · [Tabelas do banco](database.md)

## Onde começar a ler

No Docker Compose da demo, o serviço `telegram-worker` executa `python -m app.telegram_service process`. Leia `main()` até a linha 449, onde ela escolhe `run_broker_worker()` para o transporte RabbitMQ. Esse caminho cria o grafo e os clientes dos modelos uma vez por processo e registra `process_chat_turn()` para receber avisos da fila. Os serviços `relay` e `send` executam o mesmo módulo com outros argumentos. [Comando do worker no Compose](../docker-compose.yml#L112-L117) · [`main()`](../app/telegram_service.py#L408-L450) · [`run_broker_worker()`](../app/telegram_service.py#L239-L270)

| Serviço | Papel | Responsabilidade |
| --- | --- | --- |
| `api` | Webhook | Recebe o HTTP do Telegram e grava a entrada. [Comando no Compose](../docker-compose.yml#L70-L74) |
| `telegram-relay` | `relay` | Lê avisos pendentes no PostgreSQL e os publica no RabbitMQ. [Comando no Compose](../docker-compose.yml#L21-L26) |
| `telegram-worker` | `process` | Processa uma mensagem do chat e prepara a resposta. [Comando no Compose](../docker-compose.yml#L112-L117) |
| `telegram-sender` | `send` | Entrega a resposta salva usando o bot que recebeu a mensagem. [Comando no Compose](../docker-compose.yml#L146-L151) |

`run_worker()`, no mesmo módulo, executa o agendador de lembretes e os papéis que usam o transporte alternativo `--transport postgres`. Para seguir o worker da demo, abra `run_broker_worker()`. [Seleção do transporte](../app/telegram_service.py#L408-L450) · [Loop em PostgreSQL](../app/telegram_service.py#L273-L307)

### 1. O webhook grava “1” e o relay avisa o worker

`telegram_webhook()` é a função que o CAPY executa quando o Telegram faz um POST com um novo evento. Ela valida a chamada e usa `save_update()` para salvar a entrada. Para uma mensagem privada aceita, o banco confirma o recibo em `telegram_updates` e o aviso em `telegram_publications` na mesma transação. A API então responde `204`, indicando que recebeu o evento. Se o Telegram reenviar o POST, a chave `(bot_id, update_id)` mantém um só recibo por bot e evento. [`telegram_webhook()`](../app/main.py#L76-L108) · [`save_update()`](../app/channels/telegram_intake.py#L41-L102)

O relay lê os avisos pendentes com `publish_due()`. Para trabalho de entrada, `publish_wakeup()` manda ao RabbitMQ o ID do bot e o ID do chat, na forma `[bot_id, chat_id]`. O texto “1” fica no recibo. O relay só apaga o aviso pendente depois de o RabbitMQ confirmar a publicação. Se o relay parar entre a confirmação e a exclusão, pode publicar o mesmo aviso novamente. [`publish_due()`](../app/channels/telegram_publications.py#L39-L70) · [`publish_wakeup()`](../app/channels/telegram_broker.py#L62-L85) · [Confirmações no RabbitMQ](https://www.rabbitmq.com/docs/confirms)

### 2. O worker pega a próxima mensagem do chat

O RabbitMQ entrega os dois IDs a `process_chat_turn()`. Neste trecho de `telegram_service.py`, a linha 262 tenta obter um `claim`. As linhas 263–264 só iniciam o turno quando o banco concede essa autorização. [`process_chat_turn()`, linhas 260–264](../app/telegram_service.py#L260-L264)

```python
claim = turns.claim_chat(bot_id, chat_id, per_business=args.per_business)
if claim:
    process_turn(turns, claim, runtime)
```

`claim_chat()` consulta o primeiro turno pendente do chat e verifica se ele pode começar. Imagine que um áudio com o pedido de horários chegou antes do “1” e ainda está em processamento. O banco mantém esse áudio na frente. Se o chat está ocupado ou a empresa atingiu o limite de turnos simultâneos, a função registra um novo aviso para tentar depois e retorna `None`. Ao conseguir a vez, `_start_attempt()` marca o recibo como `processing` e atualiza a conversa com o ID do turno, o prazo e o número da tentativa. [`claim_chat()`](../app/channels/telegram_turns.py#L157-L218) · [Ordem das mensagens](../app/channels/telegram_turns.py#L221-L247) · [`_start_attempt()`](../app/channels/telegram_turns.py#L272-L327)

**Para que serve o objeto `TurnClaim`?** Ele reúne o bot, o registro da mensagem (`row`), o número da tentativa (`generation`), a referência ao último checkpoint publicado e os indicadores `interrupted` e `late`. O worker passa esse objeto às funções que precisam saber qual tentativa está trabalhando. O número sobe a cada claim do chat, inclusive se um worker retomar o mesmo update. A autorização de fato fica na tabela `telegram_conversations` do PostgreSQL. [`TurnClaim`](../app/channels/telegram_turns.py#L67-L116)

Por exemplo, o worker A pega o “1” com `generation=7`. Esse **lease**, ou autorização com prazo, vale por 90 segundos e o **heartbeat** tenta renová-lo a cada 30 segundos. Se o prazo expirar, um worker B pode recuperar o mesmo recibo com `generation=8`. Mesmo que A volte, `claim.assert_owned(session)` compara chat, update, geração e prazo dentro da transação de escrita. A tentativa 7 perde a autorização e recebe `ClaimLost`. Isso permite liberar o bloqueio inicial do banco durante a chamada demorada ao modelo. [Prazo do lease](../app/channels/telegram_turns.py#L28) · [Renovação em segundo plano](../app/telegram_service.py#L189-L217) · [Verificação antes de gravar](../app/channels/telegram_turns.py#L88-L116)

### 3. O worker recupera os horários e roda o grafo

`process_turn()` associa os logs ao bot e ao update. `_process_turn()` confere se a mensagem pode seguir, inicia a renovação do lease e chama `_process_private_message()`. Este é o trecho principal em `telegram_service.py`. [`process_turn()`](../app/telegram_service.py#L66-L95) · [Trecho abaixo, linhas 102–112](../app/telegram_service.py#L102-L112)

```python
worker = _prepare_turn_worker(turns, claim, runtime)
if worker is None or not _restore_turn_checkpoint(turns, claim, worker):
    return

identity = worker._ensure_customer(message["from"], claim.row["telegram_user_id"])
debug_event("telegram.turn.customer_resolved")
reply = worker._reply_to_message(claim.row, identity, message)
checkpoint_to_publish = (
    worker.checkpoint_thread if worker.checkpoint_completed else claim.checkpoint_thread
)
turns.finish(claim, reply, checkpoint_to_publish)
```

1. **Linhas 102–104. Preparar uma tentativa.** `_prepare_turn_worker()` cria um objeto `TelegramWorker` para este turno. Ele reaproveita o grafo e os clientes dos modelos e passa `claim.assert_owned` aos serviços que gravam o cliente e o agendamento. [Preparar o worker](../app/telegram_service.py#L120-L161)

   A tentativa recebe uma chave de checkpoint própria. `_restore_turn_checkpoint()` copia para ela o último estado concluído, onde consta que a opção 1 é às 14h. Assim, uma tentativa interrompida pode deixar um estado parcial em sua própria chave e a referência publicada continua apontando para a oferta anterior. `thread_id` é o nome dessa chave no LangGraph. [Copiar o estado no Redis](../app/graph/redis_checkpoint.py#L44-L58) · [Persistência do LangGraph](https://docs.langchain.com/oss/python/langgraph/persistence)

2. **Linhas 106–108. Identificar o cliente e gerar a resposta.** `_ensure_customer()` associa o remetente do Telegram ao cliente daquele bot e empresa, criando o vínculo quando necessário. Esse `customer_id` permite ao serviço gravar o agendamento em nome da pessoa certa. `_reply_to_message()` trata `/start` e cartões de contato ali mesmo. Para “1”, chama `_run_turn()`. [Vincular o cliente](../app/channels/telegram_repository.py#L37-L81) · [`_reply_to_message()`](../app/telegram_worker.py#L43-L88)
3. **Linhas 109–112. Escolher o estado que o próximo turno poderá ler.** Quando o grafo termina, o worker entrega a chave da tentativa atual para `finish()`. Em um caminho como `/start`, a resposta sai com a referência anterior, pois o grafo ficou fora desse turno. [Escolha do checkpoint](../app/telegram_service.py#L109-L112) · [Marcação ao terminar o grafo](../app/telegram_worker.py#L160-L168)

Dentro de `_run_turn()`, `_message_input()` devolve texto ou baixa a gravação indicada no update. O worker carrega o **catálogo** (serviços, profissionais e dados da empresa) e monta `TurnContext` com o cliente, a modalidade de entrada e a data local da empresa. Para operações de agendamento, usa a chave `telegram:{bot_id}:{update_id}`. Reutilizar essa chave com os mesmos dados permite recuperar o resultado de uma gravação anterior. Essa propriedade se chama **idempotência**. [`_message_input()`](../app/telegram_worker.py#L104-L125) · [`_run_turn()`](../app/telegram_worker.py#L127-L168) · [Idempotência e recuperação](idempotency.md)

`handle_turn()` passa o conteúdo bruto ao grafo pelo contexto em memória. Se a entrada for áudio, Gemma transcreve. O grafo remove dados sensíveis reconhecíveis do texto antes da interpretação e passa a mensagem interpretada e o rascunho da conversa a Laya. Para a escolha “1”, o grafo consulta os horários do checkpoint. O serviço de agendamento verifica se as 14h continuam livres e grava a reserva para o cliente. `_run_turn()` devolve `result.text` para o worker. [`handle_turn()`](../app/graph/workflow.py#L13-L40) · [Nó de interpretação](../app/graph/steps.py#L97-L135) · [Escolha do horário](../app/graph/steps.py#L569-L604) · [Gravação do agendamento](../app/booking.py#L152-L214)

### 4. O worker salva a resposta e libera o chat

`turns.finish(claim, reply, checkpoint_to_publish)` valida a autorização mais uma vez. Em uma única transação no PostgreSQL, grava a resposta em `telegram_outbox`, registra um aviso para o sender e publica a referência ao checkpoint na conversa. Também libera o lease, marca o recibo como `delivered`, descarta seu conteúdo bruto e deixa um aviso se houver outro turno pendente. O ID do recibo fica disponível para reconhecer um reenvio do webhook. [`finish()`](../app/channels/telegram_turns.py#L384-L454)

O estado `telegram_updates.status = "delivered"` indica que CAPY terminou de tratar a entrada. A entrega externa ainda depende do sender. O consumidor confirma o aviso de entrada ao RabbitMQ com `basic_ack()` quando o callback termina com sucesso, inclusive se a chamada apenas gravou um aviso para tentar mais tarde. [Confirmação ao broker](../app/channels/telegram_broker.py#L105-L134)

### 5. O sender entrega a resposta

O relay publica o ID da resposta em uma fila de saída. O callback `send_outgoing()` passa esse ID a `claim_by_id()`. Essa função reserva a entrega se a resposta estiver pronta, respeitando a ordem do chat e os limites de envio. `deliver_claimed()` chama `TelegramDelivery.send()`, que usa `send_message()` para falar com o Telegram. [`send_outgoing()`](../app/telegram_service.py#L248-L256) · [Reserva do envio](../app/channels/telegram_delivery.py#L111-L124) · [`send_message()`](../app/channels/telegram_delivery.py#L77-L80)

O sender registra `sent` quando o Telegram aceita a chamada e `ambiguous` quando perde a resposta e não consegue saber se ela foi aceita. Em caso de erro temporário que permite reenvio, reagenda a mesma resposta. A fila separada permite entregar respostas enquanto o worker de conversa está ocupado analisando áudio. [`deliver_claimed()`](../app/channels/telegram_delivery.py#L350-L383)

## Quando o caminho muda

| Situação | O que ler no código |
| --- | --- |
| O cliente respondeu “1”, mas a oferta expirou no Redis. | `_restore_turn_checkpoint()` encerra o turno com um pedido para consultar novos horários. [Restauração do checkpoint](../app/telegram_service.py#L164-L180) |
| O Redis está indisponível durante a restauração, ou faltam credenciais do bot. | O worker adia o turno por um minuto e libera o chat antes de alterar cliente ou agendamento. [Adiar o turno](../app/channels/telegram_turns.py#L349-L381) |
| Um novo worker recupera uma tentativa interrompida. | A reserva pode ter sido gravada antes da falha, pois o agendamento e `finish()` usam duas transações. O novo worker limpa a referência do checkpoint e pede ao cliente que consulte seus agendamentos. [`claim.interrupted`](../app/telegram_service.py#L85-L88) · [Teste da queda entre as gravações](../tests/integration/test_telegram_production.py#L187-L221) |
| O “1” chegou depois de o chat já ter processado uma mensagem mais nova. | `claim.late` pede uma nova oferta para evitar aplicar o número à lista errada. [`claim.late`](../app/telegram_service.py#L89-L92) |
| RabbitMQ entregou de novo o mesmo aviso. | O worker consulta o que ainda está pendente. Se o turno original terminou, o aviso pode ser consumido sem trabalho ou acordar o próximo turno do chat. [Teste da fila e de avisos duplicados](../tests/integration/test_telegram_broker.py#L61) |

Há dois sinais periódicos durante um turno demorado. A `Thread` de `_keep_turn_claim_alive()` renova o prazo do claim no PostgreSQL. No consumidor da fila, o callback roda em um `ThreadPoolExecutor` para que a conexão principal continue respondendo ao heartbeat do RabbitMQ. [Renovação do claim](../app/telegram_service.py#L189-L217) · [Conexão com o RabbitMQ](../app/channels/telegram_broker.py#L122-L149)

## Acompanhe uma mensagem nos logs

Depois de configurar um [bot de teste](../README.md#try-the-telegram-voice-demo), rode a demo com logs de depuração e responda “1” a uma oferta de horários.

```bash
CAPY_LOG_LEVEL=DEBUG docker compose --profile telegram up -d --build
docker compose --profile telegram logs -f api telegram-worker telegram-sender
```

Procure `telegram.inbox.recorded` e use o par `bot_id` e `update_id` do “1” para seguir a mesma entrada nos outros serviços. O pedido de horários tem outro `update_id`. [Como os IDs entram nos logs](../app/observability.py#L73-L91)

| Evento | O que você acabou de ver |
| --- | --- |
| `telegram.inbox.recorded` com `inserted=True` | A API gravou um novo recibo. |
| `telegram.turn.claimed` | O worker iniciou uma tentativa. `attempt` traz a geração daquela tentativa. |
| `workflow.checkpoint.forked` com `found=True` | O worker copiou o estado da oferta anterior. |
| `workflow.slot.matched` com `option_number=1` | O grafo encontrou o horário associado à escolha. |
| `availability.slot.validated` | O horário passou pela verificação atual de disponibilidade. |
| `booking.saved` | A gravação da reserva foi confirmada no banco. |
| `telegram.turn.saved` com `has_reply=True` | A resposta foi salva para o sender. |
| `telegram.outbox.result` com `outcome=sent` | O Telegram aceitou a resposta e o sender registrou o resultado. |

Esses eventos registram etapas, IDs e resultados. Para voltar ao nível padrão, reinicie os serviços com `CAPY_LOG_LEVEL=INFO`.

## Rode o exemplo sem Telegram

A avaliação usa modelos simulados e serviços de agendamento de teste. Você acompanha a interpretação e a criação de um agendamento no grafo local. Para observar o webhook, o lease e o sender, use o roteiro de logs da demo.

```bash
CAPY_LOG_LEVEL=DEBUG uv run --locked python -m app.evals.runner \
  --understanding deterministic --routing deterministic
```

No primeiro caso, `en-booking`, o cliente escreve “Book Massage”, depois “tomorrow” e por fim “1”. A partir de `eval.turn.started` com `number: 3`, acompanhe os eventos deste turno. [Conversa de teste](../evals/data/smoke-text.jsonl#L1) · [Como preparar o ambiente](../README.md#run-the-offline-example)

| Evento | O que conferir no caso `en-booking` |
| --- | --- |
| `workflow.draft.operation` | `reason: time_choice_for_new_booking` indica que a escolha faz parte de um novo agendamento. |
| `workflow.route.booking_choice` | `reason: action_does_not_target_existing_booking` indica que a opção se refere aos horários oferecidos. |
| `workflow.route.rule_applied` e `workflow.route.selected` | `reason: choose_from_offered_times` e `route: select` levam ao nó que lê a lista de horários. |
| `workflow.slot.matched` e `workflow.action.executing` | `option_number: 1` identifica a escolha. `operation: create` aciona o serviço de teste para criar o agendamento. |
| `workflow.turn.completed` | `has_booking: True` indica que o grafo devolveu um agendamento. A lista de resultados inclui `PASS  en-booking`. |

Se a etapa de roteamento falhar na demo, o campo `stage` de `workflow.route.failed` indica onde o erro surgiu. [Código do roteamento](../app/graph/steps.py#L252)

Para ver o mesmo “1” atravessar dois turnos com PostgreSQL e Redis, leia `test_completed_checkpoint_carries_slot_offer_into_next_attempt`. O teste cria uma oferta, processa a resposta e conta os agendamentos gravados. Também exercita uma oferta expirada e uma queda após gravar a reserva. [Teste de integração em Python](../tests/integration/test_telegram_production.py#L159-L221)

## Sequência das chamadas

Leia o SVG de cima para baixo. Q01–Q08 acompanham a entrada. Na saída, uma resposta do worker vai de Q07 para Q10. Q09 mostra outra origem para a mesma fila de saída, o agendador de lembretes hospedados.

![Sequência do Telegram, do webhook ao processamento e ao envio de respostas e lembretes, com setas Q01 a Q15.](assets/telegram-broker-sequence.svg)

| Seta | O que passa e por quê |
| --- | --- |
| **Q01** | O Telegram envia o update ao webhook registrado, que valida a rota e o segredo do bot. [Webhook](../app/main.py#L76-L108) |
| **Q02** | A API grava o recibo e o aviso na mesma transação para que toda entrada privada nova possa chegar ao worker. [Gravação da entrada](../app/channels/telegram_intake.py#L78-L96) |
| **Q03** | O relay reserva um aviso pronto por um prazo curto para que outro relay possa recuperá-lo se necessário. [Reserva da publicação](../app/channels/telegram_publications.py#L39-L61) |
| **Q04** | O relay envia `[bot_id, chat_id]` à fila de entrada e espera a confirmação do broker. [Publicação](../app/channels/telegram_broker.py#L62-L85) |
| **Q05** | O RabbitMQ entrega o aviso ao consumidor ativo da partição do chat. Uma partição é uma fila para um grupo fixo de chats. Outras partições podem progredir enquanto esta processa áudio. [Escolha da partição](../app/channels/telegram_broker.py#L22-L30) |
| **Q06** | O worker usa os IDs para buscar a próxima mensagem no PostgreSQL e tentar obter o lease. [Claim do chat](../app/channels/telegram_turns.py#L157-L218) |
| **Q07** | O worker salva a resposta, a referência ao checkpoint e os próximos avisos, juntos no PostgreSQL. [Conclusão do turno](../app/channels/telegram_turns.py#L384-L454) |
| **Q08** | O consumidor confirma ao RabbitMQ que tratou o aviso. Uma desconexão antes da confirmação pode provocar nova entrega. [Confirmação](../app/channels/telegram_broker.py#L122-L134) |
| **Q09** | O agendador de produção grava o envio de um lembrete que atingiu o horário programado e registra um aviso para o relay. [Agendar lembrete](../app/reminders.py#L71) |
| **Q10** | O relay publica o ID da resposta ou do lembrete em uma fila reservada para envios. [Publicação de saída](../app/channels/telegram_broker.py#L68-L74) |
| **Q11** | O RabbitMQ entrega esse ID a um sender disponível para tratá-lo. [Loop do sender](../app/telegram_service.py#L248-L256) |
| **Q12** | O sender consulta o envio salvo, verifica limites e ordem e, para lembretes, confere se a versão do lembrete ainda é válida. [Autorização de envio](../app/channels/telegram_delivery.py#L160-L184) |
| **Q13** | O sender usa o bot registrado para chamar a API do Telegram com a mensagem salva. [Envio](../app/channels/telegram_delivery.py#L77-L80) |
| **Q14** | O sender grava o resultado da chamada ou uma nova tentativa segura no PostgreSQL. [Conclusão do envio](../app/channels/telegram_delivery.py#L260-L300) |
| **Q15** | O sender confirma ao RabbitMQ que tratou o aviso. Uma nova entrega consulta o estado do mesmo envio. [Confirmação](../app/channels/telegram_broker.py#L122-L134) |

## Referências

- O exemplo começa com uma tarefa concreta e segue o que o leitor consegue reconhecer no código. O Diátaxis recomenda esse caminho para ensinar e propõe ligar as etapas aos motivos das decisões. [Tutoriais](https://diataxis.fr/tutorials/) · [Explicações](https://diataxis.fr/explanation/)
- Os detalhes de recuperação e as setas ficam disponíveis depois do fluxo principal. Essa ordem aplica a divulgação progressiva descrita no curso de escrita técnica do Google. [Organização de documentos longos](https://developers.google.com/tech-writing/two/large-docs#disclose-information-progressively)
