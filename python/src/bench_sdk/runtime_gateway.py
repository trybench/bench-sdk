"""Guest-side file transport for an offline sandbox. No credentials required."""
import json
import os
from pathlib import Path
import time

class BenchGateway:
    def __init__(self):
        self.path=Path(os.environ['BENCH_GATEWAY_PATH']);self.sequence=0
    def model(self,messages,tools=None):
        n=self.sequence;self.sequence+=1
        payload={'operation':'model','messages':messages}
        if tools:payload['tools']=tools
        temporary=self.path/f'request-{n}.tmp'
        temporary.write_text(json.dumps(payload))
        temporary.replace(self.path/f'request-{n}.json')
        deadline=time.monotonic()+115
        response=self.path/f'response-{n}.json'
        while not response.exists():
            if time.monotonic()>deadline:raise TimeoutError('Gateway response unavailable')
            time.sleep(.05)
        value=json.loads(response.read_text())
        if 'error' in value:raise RuntimeError(value['error'])
        return value['message']
