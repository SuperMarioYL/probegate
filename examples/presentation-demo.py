"""Evaluate four explicit spans with the real Python compile probe."""
import json
from probegate import ProbeGate, Span
cases=[('clear',0.1,'answer = 42'),('uncertain',0.9,'answer = 42'),('syntax_only',0.1,'def broken(:'),('both',0.9,'def broken(:')]
with ProbeGate(uncertainty_threshold=0.5,probe='compile') as gate:
    results=[]
    for i,(name,uncertainty,code) in enumerate(cases,1):
        decision=gate.guard(Span(id=name,agent_step=i,content=code,uncertainty=uncertainty))
        results.append({'case':name,'uncertainty':decision.uncertainty,'probe_passed':decision.probe.passed,'rule':decision.rule})
    print(json.dumps(results,indent=2))
