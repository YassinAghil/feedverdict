# Three-minute demo script

Aim for 2:35–2:50 so upload/player timing cannot push the video over three
minutes. Increase terminal font size before recording and close notifications.

## 0:00–0:20 — Problem and thesis

> “FeedVerdict reconciles real crypto prices from Coinbase, Kraken, and
> Bitstamp. The planner is adaptive, but the price decision is deterministic:
> it only persists a canonical value when independent evidence forms quorum.”

Show the README title and architecture diagram.

## 0:20–0:55 — Real end-to-end run

Run:

```bash
feedverdict BTC USD
```

Point out:

- real public exchange adapters and provider timestamps;
- the planner stops after two agreeing sources, so it does not call Bitstamp
  unnecessarily;
- `VERIFIED/HIGH`, the median canonical price, and `Canonical persisted: YES`.

If a live provider is genuinely down, do not hide it. Briefly say the agent is
degrading as designed, then rely on the deterministic scenario for a concise
walkthrough.

## 0:55–1:50 — Silent stale failure and adaptive fallback

Run:

```bash
feedverdict demo stale
```

Say explicitly that this is labelled deterministic fault injection. Follow the
trace:

1. Coinbase returns successfully, so ordinary HTTP error handling would miss
   the problem.
2. Its provider timestamp is 600 seconds old, above the 120-second policy.
3. The agent rejects it, fetches Kraken, then recognises one fresh source is not
   quorum and fetches Bitstamp.
4. Kraken and Bitstamp agree. It returns `VERIFIED/MEDIUM`; confidence is reduced
   because fallback was necessary.
5. Synthetic demos do not write the live canonical database.

## 1:50–2:20 — Disagreement and evaluation

Run:

```bash
feedverdict demo outlier
feedverdict eval
```

Explain that two disagreeing sources cannot identify which one is wrong, so the
planner asks a third source. The largest agreeing cluster wins; the minority is
flagged, not silently averaged. Point to the six passing decision contracts,
especially single-source and all-failed cases where no canonical update occurs.

## 2:20–2:45 — Production choices and close

Show the README’s trade-offs/next-steps section.

> “SQLite retains canonical history, full decision traces, and transparent
> source-health evidence that ranks future plans. Config-driven HTTPS JSON and
> CSV adapters make sources extensible without letting credentials into config.
> With more time I would add replay evaluation over captured production traces,
> market-specific thresholds, and OpenTelemetry. I deliberately kept LLMs out
> of the money path because this decision must be reproducible and testable.”

Stop recording before 3:00.
