# Methodology

The engine is an explainable macroeconomic research tool. It weighs evidence and expresses a stance; it does not estimate intrinsic value, expected returns, or trading profitability.

## Interpretation and calculation have different roles

Language models identify relevance, transmission channels, polarity, magnitude, and related situations. They also interpret scheduled events and write explanations from supplied records. Python calculates scores, thresholds, breadth, degree labels, instrument weights, and rankings.

This separation makes calculations reproducible from saved inputs. It does not make model judgements objective: relevance, magnitude, and situation grouping can vary between calls or model versions.

## Two axes and two time lanes

- **Policy:** evidence about tighter or looser monetary policy.
- **Directional:** growth, flows, geopolitics, supply/demand, and other economic evidence.
- **Current:** what recorded situations support at the time of a pass.
- **Expected:** what upcoming Medium/High calendar events imply if forecasts broadly occur.

One development is assigned to the axis it primarily drives. Current and expected values remain separate in storage and arithmetic. The written explanation may discuss both; it must not invent a blended numerical score.

## Magnitude and signs

| Magnitude | Interpretation |
| --- | --- |
| 1–3 | Routine information or a minor change. |
| 4–6 | A meaningful development, surprise, or shift in tone. |
| 7–8 | A shock that may stand alone as material evidence. |
| 9–10 | An exceptional disruption or regime change. |

Signs are assigned per entity. One development can support an exporter and weigh on an importer. A transmission channel must be direct enough to explain in a sentence; spreading a headline across many entities is not additional corroboration. Magnitude is a heuristic scale, not a standardized economic surprise statistic.

## Situations prevent coverage from becoming false breadth

Related headlines update one durable situation identified by an S-number. An update can change its magnitude, add evidence, or resolve it. Situations move through open, escalating, fading, and resolved states.

A story is marked fading after four days without evidence. Fading changes its status, not its decay formula. Resolved situations do not contribute. Evidence older than 60 days is excluded from the current read. A model can still accidentally split one underlying cause into several situations; identifier and duplication checks help expose that risk without proving independence.

## Recency and current scores

For situation i on a given axis:

```text
age_i = max(0, time_of_pass - last_evidence_time_i)
weight_i = 0.5^(age_i / half_life_axis)
contribution_i = magnitude_i × polarity_i × weight_i
score = Σ contribution_i
```

The half-life is **21 days for policy** and **7 days for directional evidence**. Different clocks reflect the assumption that a central-bank stance persists longer than event news. These are configurable assumptions, not estimated decay parameters. Future timestamps are clamped so they cannot amplify a contribution above its original magnitude.

## Breadth comes before direction

A material situation has an absolute weighted contribution of at least **0.5 × 3.5 = 1.75** under the defaults. Breadth is met by at least two material situations, or one material situation with original magnitude at least 7. That shock exception does not bypass the score threshold.

| Evidence condition | State |
| --- | --- |
| Nothing on file, or breadth fails | No read, with a reason. |
| Breadth passes and absolute score < 3.5 | Neutral. |
| Breadth passes and score >= +3.5 | Positive: hawkish on policy, bullish on direction. |
| Breadth passes and score <= -3.5 | Negative: dovish on policy, bearish on direction. |

A score of +6 from one magnitude-6 story remains no read. Fresh +6 and +5 situations score +11 and meet breadth. Fresh +6 and -4 score +2: enough material evidence exists, but the result is neutral.

Degree labels are **slightly** at absolute score 3.5, **moderately** at 7, and **strongly** at 12. They describe score size; they are not confidence intervals.

## From entities to instruments

For currency pairs, each axis uses the base currency's score minus the quote currency's score. If a required current leg has no read, the instrument also has no read on that axis.

Non-FX policy scores inherit USD policy through configured coefficients:

| Instrument | USD policy coefficient | Directional calculation |
| --- | ---: | --- |
| Gold, silver | -1.0 | Own entity minus USD. |
| WTI oil | -0.5 | Own entity. |
| S&P 500 | -1.0 | Own entity. |
| Nasdaq 100 | -1.3 | Own entity. |
| Dow | -0.8 | Own entity. |

Negative coefficients encode an assumed headwind from tighter US policy. Nasdaq's larger magnitude reflects an assumed greater sensitivity to rates. These coefficients are **not regression-estimated betas**; relationships can weaken or reverse under different conditions. Instrument scores receive the same threshold and degree bands after the weighted sum.

Expected instrument states use signed ordinal states (-1, 0, +1), weighted by the configured legs. They do not acquire a forecast score or degree. A leg with nothing scheduled can be left out of the expected combination; a leg with scheduled but unreadable evidence blocks that expected axis. A quiet calendar is not proof that the leg is economically neutral.

## Ranking attention

The calendar horizon is seven days. For each axis, divergence is the ordinal distance between current and expected states. A full positive-to-negative flip has distance 2; a neutral-to-directional change has distance 1. A no-read transition receives distance 1 only when the other state is directional. Identical states have distance 0.

```text
urgency = 1 / max(days_until_decisive_event, 0.25)
rank = max(policy_divergence, directional_divergence) × urgency × confidence_weight
```

Confidence weights are **HIGH 1.0 / MEDIUM 0.6 / LOW 0.3**. They prioritize inspection; HIGH does not mean a 100% chance of being correct. Ranking is deterministic given the stored states, confidence, and scheduled time. Instrument event selection prefers the highest-confidence scheduled leg, then the earlier event; this is an assumption rather than a comprehensive event-risk model.

The interface also provides heuristic stance labels such as position, lean, and wait. Those are rule summaries of agreement and event pressure, not order instructions or calibrated recommendations.

## Scientific discipline and validation status

Implemented safeguards include structured output parsing, deterministic arithmetic, injectable feeds, replay caching, explicit missing-evidence states, source-record links, stored prompts, and checks for unsupported citations. These make the system inspectable and testable. They do not prove the economic interpretation or establish a causal model.

A useful validation programme would:

1. Independently annotate relevance, axis, magnitude, and situation grouping; measure agreement and errors.
2. Repeat identical uncached prompts across runs/models to measure interpretation stability. Cached repeats cannot establish stability.
3. Vary half-lives, thresholds, breadth floors, and coefficients to identify fragile readings.
4. Evaluate expectations on held-out chronological periods, using only information available at each forecast time.
5. Compare against simpler baselines and report coverage, false directions, missed events, calibration, and regime differences.

This programme is proposed work. The repository does not provide completed predictive benchmarks, significance tests, a performance backtest, or an independently validated probability model. Archive replay uses URL-derived titles and is a context-building utility, not a leakage-controlled backtest.

## Limits to keep visible

Feeds are partial and can fail. The calendar endpoint used by the engine supplies schedule, forecast, and previous values; released outcomes are not relied on there. The pipeline uses headlines for actual developments. Language-model grouping and scoring can be wrong, and citation checks cannot detect every unsupported claim.

A fresh read is not automatically accurate. A quiet feed is not automatically a quiet market. Narrative age and current-score age can differ because unchanged text is carried forward. API costs are estimates based on configured token rates and cache usage; provider invoices are authoritative.
