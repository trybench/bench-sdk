"""Run locally: python python/examples/simulate_refund.py. No upload or model call."""
import asyncio
from bench_sdk import Bench

async def main():
    bench = Bench(api_key="bench_sk_local_example", repository="example/refunds", branch="test")
    class Session:
        def __init__(self, state, fixed):
            self.state, self.fixed = state, fixed
        def turn(self, message, context):
            with bench.trace("refund", kind="TOOL") as span:
                if not self.fixed or self.state["refunds"] == 0:
                    self.state["refunds"] += 1
                span.set_output("Refunded")
            return "Refunded"
        def observe(self):
            return self.state
        def close(self):
            self.state["refunds"] = 0
    cases = [{"id":"refund-retry", "input":{"initial_state":{"refunds":0}, "turns":["Refund order A", "Retry that refund"]}, "expected_output":"Refunded", "expected_state":{"refunds":1}}]
    options = dict(source_revision="a"*40, context_revision="refund-policy-v1", cases=cases)
    before = await bench.simulate_system(**options, create_session=lambda state, ctx: Session(state,False))
    after = await bench.simulate_system(**options, create_session=lambda state, ctx: Session(state,True))
    assert before["summary"]["score"] == 0 and after["summary"]["score"] == 100
    assert before["suite_hash"] == after["suite_hash"]
    print({"before":before["summary"], "after":after["summary"]})
    await bench.aflush()

if __name__ == "__main__":
    asyncio.run(main())
