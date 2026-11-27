"""k6 script for a concurrent burst of one operation, and parsing of its result.

The script sends every request in one ``http.batch`` call, interprets each
response with the verified mapping (success codes and the confirmation field),
and prints a JSON summary from ``handleSummary`` to stdout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SUMMARY_KEY = "raccoon"

_SCRIPT = r"""
import http from 'k6/http';
import { Counter } from 'k6/metrics';

const REQUESTS = __REQUESTS__;
const SUCCESS_CODES = __SUCCESS_CODES__;
const FIELD = __FIELD__;

export const options = {
  vus: 1,
  iterations: 1,
  batch: __BATCH__,
  batchPerHost: __BATCH_PER_HOST__,
  summaryTrendStats: ['avg'],
};

const responses = new Counter('raccoon_responses');
const noResponse = new Counter('raccoon_no_response');
const confirmed = new Counter('raccoon_confirmed');

function resolve(doc, pointer) {
  if (pointer === '') return { found: true, value: doc };
  if (!pointer.startsWith('/')) return { found: false };
  let node = doc;
  for (const raw of pointer.slice(1).split('/')) {
    const token = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (Array.isArray(node)) {
      if (!/^(0|[1-9][0-9]*)$/.test(token) || Number(token) >= node.length) return { found: false };
      node = node[Number(token)];
    } else if (node !== null && typeof node === 'object') {
      if (!Object.prototype.hasOwnProperty.call(node, token)) return { found: false };
      node = node[token];
    } else {
      return { found: false };
    }
  }
  return { found: true, value: node };
}

export default function () {
  responses.add(0);
  noResponse.add(0);
  confirmed.add(0);
  const batch = REQUESTS.map((r) => [r.method, r.url, r.body, { headers: r.headers, timeout: r.timeout }]);
  for (const res of http.batch(batch)) {
    if (res.status === 0) { noResponse.add(1); continue; }
    responses.add(1);
    if (!SUCCESS_CODES.includes(res.status)) continue;
    let doc;
    try { doc = JSON.parse(res.body); } catch (e) { continue; }
    const v = resolve(doc, FIELD.pointer);
    if (!v.found) continue;
    const ok = FIELD.has_equals ? v.value === FIELD.equals : v.value === true;
    if (ok) confirmed.add(1);
  }
}

function count(data, name) {
  const m = data.metrics[name];
  return m && m.values && typeof m.values.count === 'number' ? m.values.count : 0;
}

export function handleSummary(data) {
  const out = {};
  out['__SUMMARY_KEY__'] = {
    total: REQUESTS.length,
    responses: count(data, 'raccoon_responses'),
    no_response: count(data, 'raccoon_no_response'),
    confirmed: count(data, 'raccoon_confirmed'),
  };
  return { stdout: JSON.stringify(out) + '\n' };
}
"""


def build_script(
    requests: list[dict[str, Any]],
    *,
    success_codes: list[int],
    confirmed_field: dict[str, Any],
    batch: int,
    batch_per_host: int,
) -> str:
    """Render the k6 script. Each request: method, url, headers, body (str or None), timeout."""
    field = {
        "pointer": confirmed_field["pointer"],
        "has_equals": "equals" in confirmed_field,
        "equals": confirmed_field.get("equals"),
    }
    return (
        _SCRIPT.replace("__REQUESTS__", json.dumps(requests, ensure_ascii=False))
        .replace("__SUCCESS_CODES__", json.dumps(success_codes))
        .replace("__FIELD__", json.dumps(field, ensure_ascii=False))
        .replace("__BATCH_PER_HOST__", str(int(batch_per_host)))
        .replace("__BATCH__", str(int(batch)))
        .replace("__SUMMARY_KEY__", SUMMARY_KEY)
    )


@dataclass(frozen=True)
class BurstResult:
    total: int
    responses: int
    no_response: int
    confirmed: int


class SummaryError(ValueError):
    pass


def parse_summary(stdout: str) -> BurstResult:
    """Find the JSON summary line printed by ``handleSummary``."""
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{") or SUMMARY_KEY not in line:
            continue
        try:
            data = json.loads(line)[SUMMARY_KEY]
            return BurstResult(
                total=int(data["total"]),
                responses=int(data["responses"]),
                no_response=int(data["no_response"]),
                confirmed=int(data["confirmed"]),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as err:
            raise SummaryError(f"invalid k6 summary: {line[:200]}") from err
    raise SummaryError("k6 summary not found in output")
