# Decision-policy evaluation

Unit tests answer “does each function behave as implemented?” The evaluation
suite separately answers “does the complete agent make the safety decision we
intended under representative source failures?”

Run it with:

```bash
feedverdict eval
```

The evaluation is deterministic, offline, and exits non-zero if any contract
fails.

| Scenario | Injected condition | Expected decision |
|---|---|---|
| `healthy` | First two sources are fresh and close | Stop after two; `VERIFIED/HIGH` |
| `stale` | Primary returns HTTP-success data with a 10-minute-old provider timestamp | Reject it, consult two alternatives; `VERIFIED/MEDIUM` |
| `timeout` | Primary yields no data before its deadline | Fall back to two alternatives; `VERIFIED/MEDIUM` |
| `outlier` | First two fresh prices disagree materially | Fetch tie-breaker, exclude only the minority outlier; `VERIFIED/MEDIUM` |
| `single-source` | One fresh source and two failures | Show candidate, do not persist canonical; `UNVERIFIED_SINGLE_SOURCE/LOW` |
| `all-failed` | Timeout, network failure, and malformed payload | Refuse to guess; `NO_QUORUM/NONE` |

The suite checks status, confidence, whether canonical state may be updated, how
many sources the planner queried, and the presence of the required machine-readable
reason code. It does not test live provider uptime; adapter fixture tests cover
each documented provider schema, while live mode is an operational smoke test.

## Demo commands

```bash
feedverdict demo stale
feedverdict demo timeout
feedverdict demo outlier
```

Every demo prints a prominent “no live exchange data” banner, the injected
condition, the agent's step-by-step trace, rejected/failing source codes, and the
final persistence decision. Demo runs intentionally do not modify source-health
memory, preventing synthetic failures from influencing live source ranking.
