from bluesky.plans import count
from bluesky import RunEngine
from bluesky.callbacks.best_effort import BestEffortCallback
from databroker import Broker

bec = BestEffortCallback()
RE = RunEngine()
RE.subscribe(bec)
bec.disable_plots()
db = Broker.named("temp")
RE.subscribe(db.insert)
