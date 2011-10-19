import gevent.monkey
gevent.monkey.patch_all()

if not 'gate' in globals():
    gate = None
else:
    gate.stop()

import redis
import service
import gateway
reload(service)
reload(gateway)
import gevent

REDIS_PORT = 8192

dbA = redis.Redis(port=REDIS_PORT, db=0)
dbB = redis.Redis(port=REDIS_PORT, db=1)

serv = service.TrustedParty(dbA)
gate = gateway.Gateway(dbB, serv)
gate.start()

if not 'A' in globals():
    print 'Making keys...'
    A = gate.new_client(foaf={'fullname':'Alice'})
    B = gate.new_client(foaf={'fullname':'Bob'})
    Bkey = B.new_key()
    tx = A.issue_giftcheck("I'm bringing some apples to the lab on Friday",
                           100)
    tx = A.transfer_giftcheck({'tx': tx, 'idx': 0}, 20, Bkey)
    print 'OK'


def cycle():
    global tx
    tx = B.transfer_giftcheck({'tx': tx, 'idx': 0}, 20, Bkey)


def join(timeout=0.2):
    gevent.joinall([gate._thread], timeout=timeout)

cycle()
join()
