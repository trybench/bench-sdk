"""Headless runtime evaluation client. No browser session is required."""
from __future__ import annotations

import json
from typing import Any, Literal, TypedDict
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPRedirectHandler


class EvaluationPolicy(TypedDict, total=False):
    objective: Literal['quality','cost','latency']
    confidence: float
    power: float
    minimum_quality_gain: float
    quality_tolerance: float
    minimum_resource_saving: float
    max_cost_ratio: float
    max_latency_ratio: float
    repetitions: int
    confirmation_repetitions: int
    pilot_groups: int


class BudgetExtension(TypedDict, total=False):
    max_cost_usd: float
    max_trials: int
    max_duration_s: float


class EvaluationResult(TypedDict):
    schema_version: int
    status: str
    phase: str
    decision: str | None
    completed_trials: int


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Bench does not forward credentials through redirects')


class EvaluationClient:
    def __init__(self, base_url: str, token: str, system_id: int):
        url=urlsplit(base_url)
        if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('localhost','127.0.0.1','::1')):
            raise ValueError('Use HTTPS or loopback for Bench credentials')
        if url.username or url.password or url.query or url.fragment or type(system_id) is not int or system_id<1:
            raise ValueError('Invalid Bench API configuration')
        self.url=f'{base_url.rstrip("/")}/api/ai-systems/{system_id}'
        self.token=token
        self.opener=build_opener(_NoRedirect())

    def _request(self, path: str, method='GET', body=None, key=None) -> dict[str,Any]:
        headers={'Authorization':f'Bearer {self.token}','Content-Type':'application/json'}
        if key: headers['Idempotency-Key']=key
        request=Request(self.url+'/'+path,data=json.dumps(body,allow_nan=False).encode() if body is not None else None,headers=headers,method=method)
        with self.opener.open(request,timeout=120) as response:
            return json.load(response)

    def plan(self, spec: dict[str,Any]) -> dict[str,Any]: return self._request('evaluation-plans','POST',spec)
    def get_plan(self, plan_id: str): return self._request(f'evaluation-plans/{quote(plan_id,safe="")}')
    def run(self, plan_id: str, idempotency_key: str): return self._request('evaluation-runs','POST',{'plan_id':plan_id},idempotency_key)
    def status(self, run_id: str): return self._request(f'evaluation-runs/{quote(run_id,safe="")}')
    def events(self, run_id: str, after=0): return self._request(f'evaluation-runs/{quote(run_id,safe="")}/events?after={int(after)}')
    def cancel(self, run_id: str): return self._request(f'evaluation-runs/{quote(run_id,safe="")}/cancel','POST',{})
    def resume(self, run_id: str, idempotency_key: str, *, extend_budget: BudgetExtension | None = None, retry_ambiguous_attempt: bool = False):
        """Resume a canceled run, or continue a paused one with an explicit, audited continuation."""
        body: dict[str, Any] = {}
        if extend_budget: body['extend_budget'] = dict(extend_budget)
        if retry_ambiguous_attempt: body['retry_ambiguous_attempt'] = True
        return self._request(f'evaluation-runs/{quote(run_id,safe="")}/resume','POST',body,idempotency_key)
    def candidates(self, plan_id: str, *, run_id: str | None = None, allowed_providers: list[str] | None = None, domains: list[str] | None = None):
        """Unverified candidate configurations for a NEW plan; the saved plan is never edited."""
        body: dict[str, Any] = {}
        if run_id: body['run_id'] = run_id
        if allowed_providers: body['allowed_providers'] = list(allowed_providers)
        if domains: body['domains'] = list(domains)
        return self._request(f'evaluation-plans/{quote(plan_id,safe="")}/candidates','POST',body)
    def review_cases(self, plan_id: str): return self._request(f'evaluation-plans/{quote(plan_id,safe="")}/review')
    def review(self, plan_id: str, case_id: str, verdict: Literal['good','bad','skip'], note=''):
        return self._request(f'evaluation-plans/{quote(plan_id,safe="")}/review','POST',{'case_id':case_id,'verdict':verdict,'note':note})
