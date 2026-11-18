"""Generate an editable Excalidraw study board and a matching SVG reading copy.

Standard library only. Content reflects a static code review, not a load test.
Run from the repository root: python3 scripts/generate_study_board.py
"""
import json
import textwrap
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'docs' / 'study'
OUT.mkdir(parents=True, exist_ok=True)
E = []
W = 2400
INK = '#253047'
BLUE = '#dbeafe'
GREEN = '#dcfce7'
YELLOW = '#fef3c7'
RED = '#fee2e2'
GRAY = '#f1f5f9'
sections = []


def base(kind, x, y, w, h, **extra):
    n = len(E) + 1
    e = dict(id=f'capy-{n:04d}', type=kind, x=x, y=y, width=w, height=h,
             angle=0, strokeColor=INK, backgroundColor='transparent', fillStyle='solid',
             strokeWidth=1.5, strokeStyle='solid', roughness=1, opacity=100,
             groupIds=[], frameId=None, roundness=None, seed=n * 1777, version=1,
             versionNonce=n * 311, isDeleted=False, boundElements=None,
             updated=1794484800000, created=1794484800000, link=None, locked=False)
    e.update(extra)
    E.append(e)
    return e


def rect(x, y, w, h, fill='white', **kw):
    return base('rectangle', x, y, w, h, backgroundColor=fill, **kw)


def txt(x, y, text, width=1000, size=22, color=INK, title=False):
    # Conservative widths also leave room when Excalidraw recalculates text metrics.
    cols = max(8, int(width / (size * .61)))
    lines = []
    for line in text.split('\n'):
        lines.extend(textwrap.wrap(line, cols, break_long_words=True, break_on_hyphens=False) or [''])
    text = '\n'.join(lines)
    return base('text', x, y, width, len(lines) * size * 1.3,
                strokeColor=color, strokeWidth=1, roughness=0,
                text=text, originalText=text, fontSize=size, fontFamily=1 if title else 2,
                textAlign='left', verticalAlign='top', containerId=None,
                autoResize=False, lineHeight=1.3, baseline=size)


def card(x, y, w, title, body, fill=GRAY, min_h=0):
    start = len(E)
    background = rect(x, y, w, 1, fill)
    heading = txt(x + 24, y + 20, title, w - 48, 26, title=True)
    content = txt(x + 24, heading['y'] + heading['height'] + 16, body, w - 48, 22)
    background['height'] = max(min_h, content['y'] + content['height'] + 24 - y)
    for element in E[start:]:
        element['groupIds'] = [background['id'] + '-group']
    return background


def section(y, number, title, subtitle):
    sections.append((y, number, title))
    rect(40, y, W-80, 8, '#facc15', strokeWidth=0)
    txt(60, y+28, f'{number}  {title}', W-120, 38, title=True)
    txt(60, y+90, subtitle, W-120, 22, '#475569')
    return y+155


def arrow(x1, y1, x2, y2, label='', dashed=False):
    a = base('arrow', x1, y1, abs(x2-x1), abs(y2-y1),
             points=[[0, 0], [x2-x1, y2-y1]], startBinding=None, endBinding=None,
             startArrowhead=None, endArrowhead='arrow', elbowed=False,
             strokeStyle='dashed' if dashed else 'solid')
    if label:
        txt(min(x1,x2)+10, min(y1,y2)-34, label, max(abs(x2-x1)-20, 180), 18)
    return a


def table(x, y, width, headers, rows, fractions=None, size=21):
    fractions = fractions or [1 / len(headers)] * len(headers)
    column_widths = [width * fraction for fraction in fractions]
    row_y = y
    for row_index, row in enumerate([headers] + rows):
        column_x = x
        cells = []
        for value, column_width in zip(row, column_widths):
            fill = BLUE if row_index == 0 else ('white' if row_index % 2 else GRAY)
            background = rect(column_x, row_y, column_width, 1, fill, roughness=0)
            label = txt(column_x + 16, row_y + 14, value, column_width - 32, size)
            cells.append((background, label))
            column_x += column_width
        row_height = max(label['height'] for _, label in cells) + 28
        for background, _ in cells:
            background['height'] = row_height
        row_y += row_height
    return row_y


txt(60,40,'CAPY / System design study board',W-120,54,title=True)
txt(60,125,'100,000 monthly active users | Telegram scheduling | Code review: 12 Nov 2026',W-120,26)
txt(60,180,'Read top to bottom. Every card, label and arrow is editable. Blue = current facts; green = keep / proposed direction; yellow = assumption / trade-off; red = risk. Architecture proposals are NOT implemented.',W-120,22)

y=section(300,'01','Requirements: what are we solving?','Your reference layout, adapted to CAPY. Non-functional requirements describe quality and capacity, not excluded features.')
a=card(60,y,1110,'FUNCTIONAL / what the customer can do',
       'Find available slots; book; look up upcoming appointments; confirm, cancel and reschedule.\n\nAsk about services and prices. Receive text and voice through Telegram. Reply in text and voice.\n\nMultiple businesses: each customer, catalog, schedule and booking must stay in the correct tenant.\n\nCurrent gaps: voice replies are not implemented; FAQ answers use catalog fields and fixed templates, not a general knowledge base.',BLUE)
b=card(1230,y,1110,'QUALITY / define what success means',
       '100k monthly ACTIVE users across businesses. This is not 100k concurrent requests.\n\np95 text reply <= 5 seconds: webhook receipt to Telegram accepting the reply. Voice must be benchmarked by recording duration.\n\n99.9% daily availability: time-based equivalent is 86.4 seconds unavailable per day. A monthly window is different.\n\nTrack failed scheduling journeys, not just HTTP 200 acknowledgments. An error reply is not a successful booking.',YELLOW)
y=max(a['y']+a['height'],b['y']+b['height'])+70
y=section(y,'02','Capacity: why 11 workers is only an example','Start with demand and processing time. Monthly users alone cannot determine replica count.')
end=table(60,y,2280,['Assumption / calculation','Result','Why it matters'],[
 ['100,000 users x 3 conversations x 8 incoming turns','2.4 million turns/month','Planning assumptions; replace with observed traffic.'],
 ['2.4M / (30 x 24 x 3,600)','0.93 turns/second average','Daily peaks can be much higher than the average.'],
 ['Chosen initial peak / stress scenario','20 / 100 turns per second','Neither is a measured production requirement.'],
 ['Little\'s Law: L = arrival rate x mean time','20/s x 3s = 60 in progress','Processing-only time counts active work. Total elapsed time includes the queue.'],
 ['8 safe slots/worker x 70% target occupancy','5.6 effective slots/worker','70% is a planning target for concurrency, not necessarily CPU.'],
 ['ceil(60 / 5.6)','11 workers','Requires 8-way concurrency and 3s service time to be proven.'],
 ['10% audio x 2.4M x 15 seconds','1,000 input-audio hours/month','Audio duration, tokens and GPU capacity drive inference cost.'],
 ],[.43,.23,.34])
card(60,end+25,2280,'WHY HEADROOM?',
     'A cafe receiving 2 customers/minute, each staying 10 minutes, has about 20 customers inside on average. CAPY is the same queueing idea. Averages do not describe bursts or p95 latency. Keep warm spare capacity; autoscaling reacts after demand changes.',YELLOW)
y=end+245
y=section(y,'03','Architecture: current implementation vs proposed scaling','Keep one application codebase. Separate process roles only where they need different concurrency, resources or failure handling.')
a=card(60,y,700,'CURRENT: one Telegram worker',
       'Poll Telegram -> persist update -> process graph -> save reply -> send reply.\n\nOne advisory lock per bot. Turns run sequentially. Audio, model calls and delivery share the loop.\n\napp/main.py serves health only. More API replicas do not increase conversation throughput.\n\nGood MVP property: simple ownership and conservative crash behavior.',BLUE,min_h=510)
card(60,y+550,700,'WHY split ingress and processing?',
     'A webhook should acknowledge after durable storage, not wait for inference. Workers can then process many independent conversations.\n\nDo not remove the single-worker lock until conversation serialization, safe claims and recovery exist.',YELLOW)
nodes={}
def node(name,x,yy,w,title,body,fill=GREEN,h=130):
    nodes[name]=card(x,yy,w,title,body,fill,min_h=h)
node('tg',850,y,650,'Telegram','Input: text or voice',BLUE)
node('api',850,y+230,650,'Load balancer + webhook API','Verify identity; insert update; acknowledge.')
node('db',1630,y+230,650,'PostgreSQL inbox','Existing telegram_updates can be extended.',BLUE)
node('workers',1630,y+490,650,'Conversation workers','One active turn per conversation; many users in parallel.')
node('models',850,y+490,650,'Gemma + Laya endpoints','Independent inference capacity; Python decides whether writes are allowed.',BLUE)
node('state',850,y+770,650,'Redis checkpoints','Draft and offered slots; expiring, not booking truth.',BLUE)
node('out',1630,y+770,650,'PostgreSQL + reply delivery','Domain transactions -> saved reply -> rate-limited sender.')
arrow(1175,y+150,1175,y+230)
arrow(1500,y+305,1630,y+305)
arrow(1955,y+405,1955,y+490)
arrow(1630,y+570,1500,y+570)
arrow(1955,y+690,1955,y+770)
base('arrow',1630,y+650,130,205,
     points=[[0,0],[-65,0],[-65,205],[-130,205]],
     startBinding=None,endBinding=None,startArrowhead=None,endArrowhead='arrow',elbowed=False)
txt(850,y+1015,'Arrows show the main work path. PostgreSQL is ONE database; Python domain services run inside workers. Senders return replies to Telegram. Audio preparation and reminders use separate bounded work pools.',1430,22)
y=y+1190
y=section(y,'04','Sequence: booking, retries and the crash window','A durable inbox prevents lost accepted messages. Idempotent writes prevent repeated business effects. Neither guarantees exactly-once Telegram delivery.')
actors=['Telegram','Webhook API','Worker + models','PostgreSQL','Reply sender']
xs=[230,670,1110,1550,1990]
for x,name in zip(xs,actors):
    rect(x-155,y,310,65,BLUE)
    txt(x-140,y+15,name,290,22)
    base('line',x,y+75,0,680,points=[[0,0],[0,680]],strokeStyle='dashed')
events=[(0,1,'1. POST update',False),(1,3,'2. Insert inbox; unique bot + update',False),
        (1,0,'3. ACK only after commit',True),(2,3,'4. Claim conversation + oldest update',False),
        (2,3,'5. After Gemma/Laya: validate + commit booking',False),
        (2,3,'6. Save final reply; finish update',False),(4,3,'7. Claim saved reply',False),
        (4,0,'8. Send response; record accepted delivery',False)]
for i,(src,dst,label,dash) in enumerate(events):
    yy=y+125+i*80
    arrow(xs[src],yy,xs[dst],yy,dashed=dash)
    txt(min(xs[src],xs[dst])+15,yy-28,label,abs(xs[dst]-xs[src])-30,18)
a=card(60,y+805,1110,'CRASH AFTER BOOKING, BEFORE REPLY',
       'Current: recover_interrupted() sends a check-your-appointments message instead of replaying the graph. Safe, but less convenient.\n\nProposed: save an immutable command; commit its outcome with the booking; reconstruct the reply from that outcome. Never reinterpret a possibly committed turn as a different action.',YELLOW)
b=card(1230,y+805,1110,'WHY THE SAME USER MUST BE SERIALIZED',
       'Message A: show tomorrow\'s slots. Message B: choose option 1. If B runs before A finishes, it may read an old offer.\n\nSerialize by business + bot + user. Preserve order for one conversation, not globally for the entire bot. Handle late arrivals explicitly; per-user locking alone does not reorder webhooks.',GREEN)
y=max(a['y']+a['height'],b['y']+b['height'])+70
y=section(y,'05','Database relationships: read the business model first','15 application tables after migrations 0001-0003; excludes Alembic version metadata. PK = primary key; FK = foreign key; 1:N = one-to-many.')
txt(60,y,'A. Catalog, professionals and scheduling',1100,28,title=True)
for name,x,yy in [('businesses',350,y+80),('professionals',80,y+290),('services',780,y+290),('professional_services',350,y+510)]:
    rect(x,yy,390,90,BLUE);txt(x+18,yy+25,name,355,22)
arrow(460,y+170,275,y+290,'1:N')
arrow(660,y+170,975,y+290,'1:N')
arrow(275,y+380,450,y+510,'1:N')
arrow(975,y+380,650,y+510,'1:N')
card(60,y+670,1110,'WHY a join table?',
     'One professional can offer several services. One service can be offered by several professionals. professional_services models this many-to-many relationship without copying names or prices.\n\nProfessionals also have 1:N weekly_availability_rules and 1:N blocked_periods: recurring opening hours and dated exceptions are different concepts.',GREEN)
txt(1230,y,'B. Booking and delivery',1100,28,title=True)
for name,x,yy in [('customers',1230,y+80),('bookings',1820,y+80),('reminders',1820,y+330),('reminder_inbox',1820,y+570),('telegram_identities',1230,y+330)]:
    rect(x,yy,460,90,BLUE);txt(x+18,yy+25,name,425,22)
arrow(1690,y+125,1820,y+125,'1:N')
arrow(2050,y+170,2050,y+330,'1:N')
arrow(2050,y+420,2050,y+570,'1:0..1')
arrow(1460,y+170,1460,y+330,'1:N*')
card(1230,y+725,1110,'MORE EDGES / important qualifications',
     'Each booking references one business, service, professional and customer.\n\n*Each identity links to 0..1 customer. The unique index permits at most one identity per business + bot + customer; a customer can link across bots.\n\nreminder_inbox is a demo sink. idempotency_records.resource_id is a logical booking link, NOT a declared FK.',YELLOW)
y=y+1150
y=section(y,'06','Table dictionary: what each table stores and why','Current columns after migration 0003. Lists group fields for readability. Every business_id below is an FK to businesses. Decision cards are recommendations, not applied migrations.')
DATA=[
('businesses','KEEP / tenant root','PK id (text)\nname, category, timezone\nminimum_notice_minutes\nbooking_horizon_days\nreminder_lead_minutes\ncreated_at',
 'Tenant policies and local-time rules. One business has many services, professionals, customers and bookings.'),
('professionals','KEEP / bookable person','PK id; FK business_id\nname; active\nUNIQUE(id, business_id)',
 'Separate people because conflicts belong to a resource. Two professionals can work at the same time.'),
('services','KEEP / catalog','PK id; FK business_id\nname; description; active\nduration_minutes\nbuffer_before/after_minutes\nprice_minor; currency\nUNIQUE(id, business_id)',
 'One price/duration definition reused by many bookings. Store money in minor units, not floats.'),
('professional_services','KEEP / many-to-many','PK(professional_id, service_id)\nFK business_id\nFK professional_id\nFK service_id',
 'Records which professional can perform which service. Independent FKs do not enforce matching tenants.'),
('weekly_availability_rules','KEEP / recurring hours','PK id; FK business_id\nFK professional_id\nweekday (0..6)\nopens_at; closes_at\nCHECK closes_at > opens_at',
 'One row per weekly interval. Multiple rows support a lunch break. Not precomputed appointment slots.'),
('blocked_periods','KEEP / dated exceptions','PK id; FK business_id\nFK professional_id\nstarts_at; ends_at; reason\nCHECK ends_at > starts_at\nGiST professional/range index',
 'Vacation or a one-off closure. Keep distinct from repeating weekly hours; they have different validation.'),
('customers','KEEP / domain identity','PK id; FK business_id\nfull_name; phone?; email?\ncreated_at\nPhone is nullable and NOT unique',
 'Booking ownership must survive contact changes. Migration 0003 removed phone-based uniqueness.'),
('bookings','KEEP / business truth','PK id; FK business_id\nFK service_id; professional_id\nFK customer_id\nstarts_at; ends_at\noccupied_from; occupied_until\nstatus; created_at; updated_at',
 'Occupied range includes buffers. GiST exclusion prevents overlapping active bookings. Token fields were removed in 0003.'),
('reminders','KEEP if reminders in scope','PK id; FK business_id\nFK booking_id\nsend_at; status; attempts\nclaimed_at?; delivered_at?\nlast_error?; created_at',
 'Durable scheduled work with retries. Partial unique index: at most one pending reminder per booking.'),
('reminder_inbox','DEMO ONLY / replace later','PK id; FK business_id\nFK booking_id\nUNIQUE FK reminder_id\nmessage; delivered_at',
 'Records simulated delivery for local CLI/tests. Replace with real transport delivery; do not delete before migrating callers.'),
('idempotency_records','KEEP / retry safety','PK(business_id, operation,\n   idempotency_key)\nrequest_hash; resource_id\nresult_code; created_at',
 'Same command must not repeat a mutation. Different payload with the same key is rejected. No resource_id FK today.'),
('audit_events','REMOVE or implement','PK id; FK business_id\nnullable FK booking_id\naction; metadata JSONB\noccurred_at',
 'No application reader/writer found in this review. Empty infrastructure provides no audit trail. Check external usage/data first.'),
('telegram_identities','KEEP / channel ownership','PK(business_id, bot_id,\n   telegram_user_id)\nusername; customer_id? FK\ncontact_phone?; display_name?\ncreated_at; updated_at',
 'Maps authenticated Telegram identity to a customer. A shared phone never grants account access.'),
('telegram_updates','KEEP / inbox + saved reply','PK(bot_id, update_id)\nFK business_id; chat_id?\ntelegram_user_id?; status\npayload?; reply_text?; reply_markup?\nattempts; next_attempt_at\nlast_error?; received_at; expires_at',
 'Durable deduplication, work state and reply retries already live together. No FK to telegram_identities; IDs also record ignored updates.'),
('telegram_poll_offsets','KEEP now; drop after cutover','PK bot_id\nnext_update_id\nupdated_at\nNo bot registry FK today',
 'Stores the polling cursor. Webhooks remove its runtime purpose, but only after polling is fully retired.'),
]
for i,(name,decision,fields,why) in enumerate(DATA):
    col=i%3; row=i//3
    x=60+col*770; yy=y+row*630
    fill=YELLOW if ('REMOVE' in decision or 'DEMO' in decision or 'cutover' in decision) else BLUE
    r=rect(x,yy,740,590,fill)
    txt(x+20,yy+18,name,700,25,title=True)
    txt(x+20,yy+66,decision,700,20,'#166534')
    f=txt(x+20,yy+115,fields,700,21)
    txt(x+20,yy+365,'WHY / '+why,700,21)
y=y+5*630+20
y=section(y,'07','Do we need all these tables?','My assessment: mostly sensible normalization, with a few removable pieces. Table count is not a useful proxy for operational complexity.')
end=table(60,y,2280,['Decision','Reason / trade-off','Condition before changing'],[
 ['Keep the 8 domain tables','Businesses, professionals, services, assignments, weekly hours, blocks, customers, bookings model separate facts.','Merge only if the product permanently drops that concept.'],
 ['Keep idempotency + updates','Update deduplication protects transport; command idempotency protects business mutations. They have different keys and lifecycles.','Do not remove one just because both mention duplicates.'],
 ['Keep identities separate by default','Makes channel ownership independent of customer contact information.','Merging saves one table only if one bot/account per customer is a permanent constraint and legacy links are migrated.'],
 ['Remove or implement audit_events','Only schema references found; currently no working audit feature.','Check real data and external consumers. Use a new forward migration.'],
 ['Replace demo reminder_inbox','Production delivery needs a transport outbox, not a fake inbox.','Migrate CLI/tests and delivery. May replace one table with one table.'],
 ['Retire telegram_poll_offsets','Webhook ingestion does not need a long-poll cursor.','After webhook cutover and rollback window.'],
 ['Do not split every concept into a service','One repository with API/worker/sender roles is enough initially.','Split further for measured resource or ownership needs.'],
 ],[.24,.41,.35])
card(60,end+25,2280,'COUNT THE TRADE-OFF HONESTLY',
     'Removing unused audit_events and, after webhook cutover, polling offsets takes 15 tables to 13. Replacing reminder_inbox with a real outbox keeps the count at 13. New durable leases, commands or tenant configuration may add tables again. Optimize responsibilities and correctness, not an arbitrary table target.',GREEN)
y=end+255
y=section(y,'08','Static code review: problems and evidence','These are source-level findings. Performance risks are not benchmark results; a database constraint gap is not proof of an exploitable customer endpoint.')
FINDINGS=[
 ('HIGH / before horizontal workers','One worker per bot + sequential turns',
  'app/telegram_worker.py: run(), _process_pending_updates(); telegram_repository.py: acquire_worker_lock().',
  'Adding replicas fails the bot lock; slow audio/model work delays everyone. Split roles and serialize only per conversation.'),
 ('HIGH / before concurrent delivery','Global reply head-of-line blocking',
  'app/channels/telegram_repository.py: ready_replies().',
  'The oldest reply across the bot blocks newer replies, including other chats. Use per-chat ordering plus safe sender claims.'),
 ('HIGH / integrity hardening','Tenant consistency is not enforced by composite FKs',
  'alembic/versions/0001_general_scheduling.py; 0002_telegram_channel.py.',
  'A booking can reference business A and a customer from B at the schema level. Add composite tenant FKs after cleaning data; retain application ownership checks.'),
 ('HIGH / before concurrent recovery','Recovery and checkpoints assume a single owner',
  'telegram_repository.py: recover_interrupted(); graph/redis_checkpoint.py: put(), put_writes().',
  'Recovery sweeps every processing update for the bot. Redis read-modify-write is not fenced. Add per-conversation leases, expiry and stale-owner rejection.'),
 ('MEDIUM / measure hot tenants','Business-wide write lock',
  'app/booking.py: _lock_business_and_check_retry().',
  'All booking writes for one business serialize. Deliberate MVP trade-off; measure lock wait before narrowing locks. Keep exclusion constraints.'),
 ('MEDIUM / database efficiency','Connection churn and per-professional queries',
  'app/database.py: connect(); app/repositories.py: scheduling_data().',
  'New DB connections per operation; approximately 3 + 3P queries inside scheduling_data for P professionals. Add bounded pooling; batch queries if profiling justifies it.'),
 ('MEDIUM / retention semantics','delete_thread removes only latest default-namespace state',
  'app/graph/redis_checkpoint.py: delete_thread().',
  'Earlier checkpoint/write keys and other namespaces remain until TTL. Fine for eventual expiry, insufficient if full immediate deletion is promised.'),
 ('MEDIUM / deploy safety','Migrations and demo seeding on API startup',
  'docker-compose.yml: api.command.',
  'Fine for local Compose. Do not copy unchanged into multiple production replicas; use a one-time release migration job.'),
]
for i,(severity,title,evidence,impact) in enumerate(FINDINGS):
    x=60+(i%2)*1170; yy=y+(i//2)*390
    card(x,yy,1110,severity+' / '+title,evidence+'\n\n'+impact,RED if severity.startswith('HIGH') else YELLOW,min_h=355)
y=y+4*390+35
y=section(y,'09','Why constraints matter: prevent invalid state at the boundary','Application authorization answers who may act. Database constraints answer what rows are valid. You need both.')
a=card(60,y,1110,'CURRENT GAP / a concrete example',
       'Business A owns booking X. Customer C belongs to business B.\n\nIndependent FKs accept A and C because each exists. They do not check that C belongs to A.\n\nApplication create/ownership paths do check business/customer identity. The gap matters for bugs, imports and future writers.\n\nProposed: UNIQUE(id, business_id) on parents and FOREIGN KEY(customer_id, business_id) referencing customers(id, business_id). Extend the pattern to other tenant links.',YELLOW)
b=card(1230,y,1110,'KEEP / overlap + idempotency',
       'Two users can both see 10:00 as free. Search is only a snapshot. Revalidate when booking and keep the exclusion constraint as the final race-safe guard.\n\nThe occupied interval includes preparation/cleanup buffers. Adjacent half-open ranges [start, end) do not overlap.\n\nA retry with the same operation key should reuse the committed result. Today replay reads the current booking row, so it is not an immutable copy of the original response.',GREEN)
y=max(a['y']+a['height'],b['y']+b['height'])+65
y=section(y,'10','Autoscaling and failures: what changes the replica count?','More workers help only when the downstream database and model servers have spare capacity.')
end=table(60,y,2280,['Component','Signal / starting policy','Why / failure behavior'],[
 ['Webhook API','At least 2 warm replicas; request concurrency + latency','Acknowledge only committed inbox rows. DB failure must not produce a false success.'],
 ['Text workers','Runnable conversations + arrival rate x service time','Do not count 100 queued turns for one user as 100 parallel tasks. Drain in-flight work on shutdown.'],
 ['Gemma + Laya','Queue time, tokens/sec, latency, device memory','More application pods cannot fix saturated inference. Warm redundancy is part of availability.'],
 ['Audio / TTS','Queued audio seconds + duration buckets','Isolate expensive audio. Complete transcript before actions. TTS failure falls back to saved text.'],
 ['Reply sender','Eligible reply backlog, per-bot/chat allowance','Honor 429 retry_after. More senders cannot raise Telegram limits. Prioritize interactive replies.'],
 ['PostgreSQL + Redis','Connections, lock wait, query time; checkpoint bytes','HA/failover plus restore tests. Redis loss means rebuild a draft, never invent a booking.'],
 ],[.2,.37,.43])
card(60,end+25,2280,'DO NOT OVERBUILD',
     'Start with the existing PostgreSQL ledger as the durable queue. No Kafka, database sharding, separate booking microservice, vector database or multi-region writes solely because there are 100k monthly users. Add a broker when queue load, fan-out or operational needs justify it. Gemma and Laya are required here; optimize and benchmark their boundary rather than silently removing one.',GREEN)
y=end+290
y=section(y,'11','Study checklist and implementation order','The board proposes changes; it does not modify application behavior or remove database tables.')
a=card(60,y,1110,'BUILD IN THIS ORDER',
       '1. Measure traffic, model latency, DB queries/locks and per-bot delivery.\n2. Harden tenant constraints and retry tests.\n3. Add durable webhook ingestion and per-conversation ownership.\n4. Separate senders; remove global reply blocking.\n5. Add bounded concurrency, pooling and safe crash recovery.\n6. Implement real reminders, TTS and approved FAQ content.\n7. Load-test 1, 5, 20 and 100 turns/s; tune autoscaling.\n8. Remove obsolete tables only with data/consumer checks and forward migrations.',GREEN)
b=card(1230,y,1110,'CHECK YOUR UNDERSTANDING',
       'Why can a webhook return 200 before the customer gets a reply?\nWhy do updates and commands need different idempotency keys?\nWhy is 100k MAU insufficient to size workers?\nWhy is Redis not the source of booking truth?\nWhy can one delayed reply block unrelated users today?\nWhy do separate FKs not guarantee the same tenant?\nWhich table would you remove first, and what would you check?\nWhat happens if a worker dies just after the booking commits?',YELLOW)
y=max(a['y']+a['height'],b['y']+b['height'])+50
txt(60,y,'Sources and scope',2280,28,title=True)
txt(60,y+55,'Source review: migrations 0001-0003; app/booking.py; app/scheduling.py; app/repositories.py; app/database.py; app/telegram_worker.py; app/channels/telegram_repository.py; app/graph/redis_checkpoint.py; app/reminders.py; docker-compose.yml. Related design: docs/system-design-100k.md. No live database or production load was inspected.',2280,21)
txt(60,y+180,'References: https://www.postgresql.org/docs/17/ddl-constraints.html | https://core.telegram.org/bots/faq | https://core.telegram.org/bots/api | https://keda.sh/docs/2.21/reference/scaledobject-spec/ | https://github.com/excalidraw/excalidraw/blob/master/dev-docs/docs/codebase/json-schema.mdx',2280,19)
H=y+340

scene={'type':'excalidraw','version':2,'source':'https://excalidraw.com',
       'elements':E,'appState':{'viewBackgroundColor':'#fafaf9','gridSize':None},'files':{}}
(OUT/'capy-system-design.excalidraw').write_text(json.dumps(scene,ensure_ascii=False,indent=2))

def svg(elements,top=0,height=H):
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{height}" viewBox="0 {top} {W} {height}">',
           f'<rect x="0" y="{top}" width="{W}" height="{height}" fill="#fafaf9"/>',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#253047"/></marker></defs>']
    for e in elements:
        x,yy,w,h=e['x'],e['y'],e['width'],e['height']
        if yy+h<top or yy>top+height: continue
        if e['type']=='rectangle':
            parts.append(f'<rect x="{x}" y="{yy}" width="{w}" height="{h}" rx="5" fill="{e["backgroundColor"]}" stroke="{e["strokeColor"]}" stroke-width="{e["strokeWidth"]}"/>')
        elif e['type']=='text':
            for j,line in enumerate(e['text'].split('\n')):
                size=e['fontSize'];weight='600' if e['fontFamily']==1 else '400'
                parts.append(f'<text x="{x}" y="{yy+size+j*size*1.3}" font-family="Helvetica,Arial,sans-serif" font-size="{size}" font-weight="{weight}" fill="{e["strokeColor"]}">{escape(line)}</text>')
        elif e['type'] in ('arrow','line'):
            pts=' '.join(f'{x+dx},{yy+dy}' for dx,dy in e['points'])
            dash='stroke-dasharray="8 6"' if e['strokeStyle']=='dashed' else ''
            marker='marker-end="url(#arrow)"' if e['type']=='arrow' else ''
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{INK}" stroke-width="2" {dash} {marker}/>')
    return '\n'.join(parts+['</svg>'])

(OUT/'capy-system-design.svg').write_text(svg(E))
# Keep the per-section review images outside the published study-board directory.
tmp=Path('/tmp/capy-study-preview');tmp.mkdir(exist_ok=True)
for i,(top,num,title) in enumerate(sections):
    bottom=sections[i+1][0] if i+1<len(sections) else H
    (tmp/f'section-{num}.svg').write_text(svg(E,top,bottom-top))

assert len({e['id'] for e in E})==len(E)
assert all(e['width']>=0 and e['height']>=0 for e in E)
rectangles=[e for e in E if e['type']=='rectangle' and e['height']>10]
for i,a in enumerate(rectangles):
    for b in rectangles[i+1:]:
        dx=min(a['x']+a['width'],b['x']+b['width'])-max(a['x'],b['x'])
        dy=min(a['y']+a['height'],b['y']+b['height'])-max(a['y'],b['y'])
        assert dx<=1 or dy<=1,('overlapping cards',a['id'],b['id'])
groups={}
for e in E:
    for gid in e['groupIds']: groups.setdefault(gid,[]).append(e)
for group in groups.values():
    r=group[0]
    for e in group[1:]:
        assert e['y']+e['height']<=r['y']+r['height']-10,(r['id'],e['id'])
print(json.dumps({'elements':len(E),'sections':len(sections),'width':W,'height':H,'output':str(OUT)}))
