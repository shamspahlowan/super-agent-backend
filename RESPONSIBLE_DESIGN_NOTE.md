# Responsible Design Note

## Super Agent Liquidity & Risk Intelligence Platform

## 1. Purpose of this document

This document explains how the prototype is designed to remain useful, measurable, and safe while supporting multi-provider mobile financial service operations.

The platform is an **advisory decision-support system**. It helps users understand:

- shared physical cash pressure,
- provider-specific e-money pressure,
- unusual transaction or balance behaviour,
- provider-feed reliability,
- who should receive an alert,
- who should own the case,
- and what safe next step should be considered.

It does **not** execute financial transactions, control provider wallets, declare fraud, block users, freeze funds, or bypass provider authority.

The central responsible-design principle is:

> **The system may detect, explain, notify, assign, escalate, and track — but all consequential financial or investigative decisions remain with authorized humans and provider processes.**

---

## 2. System decision boundary

The prototype supports operational awareness and coordination.

It may:

- estimate when shared cash or a provider-specific balance may become unsafe,
- identify unusual patterns that deserve review,
- show evidence and confidence,
- lower confidence when data is incomplete or conflicting,
- route alerts to an operational role,
- create and update coordination cases,
- record acknowledgements, assignments, notes, escalation, and resolution,
- generate role-appropriate explanations,
- and preserve an audit trail.

It may not:

- transfer money,
- refill a provider wallet,
- convert one provider’s balance into another,
- reverse transactions,
- block or suspend accounts,
- freeze funds,
- accuse an agent or customer,
- make a final fraud determination,
- or access real financial infrastructure.

This separation is intentional. It keeps the prototype within the challenge scope and prevents analytical signals from becoming unauthorized actions.

---

## 3. Advisory, not autonomous

All liquidity and anomaly outputs are advisory.

A forecast such as:

```text
bKash e-money may reach the safety threshold in approximately 35 minutes
```

is presented as an estimate based on recent transaction flow, current balances, and data quality.

An anomaly output such as:

```text
Repeated near-identical amounts and account concentration require review
```

is presented as a reason for human attention, not as proof of fraud.

The system uses careful language such as:

- unusual activity,
- requires review,
- possible demand spike,
- provider data conflict,
- reduced confidence,
- safe fallback,
- human review required.

The system intentionally avoids language such as:

- fraud detected,
- criminal activity confirmed,
- fraudulent account,
- block immediately,
- freeze funds,
- guilty agent.

---

## 4. Human review

Human review is mandatory for any risk-related judgment.

The platform can identify a pattern and provide:

- the relevant provider,
- the affected agent,
- contributing evidence,
- confidence,
- uncertainty,
- possible normal explanations,
- and a recommended next step.

A human operator must decide whether the alert represents:

- legitimate demand,
- an operational shortage,
- a data-quality problem,
- a provider reconciliation issue,
- activity that deserves specialist review,
- or no further action.

The workflow is designed so that a case can move through:

```text
OPEN
→ ACKNOWLEDGED
→ ASSIGNED
→ IN_REVIEW
→ ESCALATED or RESOLVED
→ CLOSED
```

There is deliberately no state named:

```text
FRAUD_CONFIRMED
```

A serious case may be escalated to an external or authorized compliance process, but the prototype itself stops before the final fraud decision.

---

## 5. False-positive awareness

The system is designed around the assumption that unusual behaviour may have legitimate causes.

Possible false-positive explanations include:

- Eid or festival demand,
- salary-day activity,
- merchant payout patterns,
- temporary provider campaigns,
- local market events,
- a small customer group repeatedly using the same agent,
- provider feed delay,
- delayed reconciliation,
- account reuse within a household or business,
- transaction retries caused by network failure,
- and temporary cash imbalance.

For this reason, every important anomaly should expose:

- the reason it was flagged,
- the evidence that contributed to the score,
- the confidence level,
- uncertainty,
- and at least one possible normal explanation where relevant.

High transaction volume alone should not automatically trigger a fraud-style review. Context, diversity, timing, account concentration, and data quality are considered together.

The synthetic validation set includes legitimate-demand hard negatives so the system can be tested for over-alerting.

---

## 6. Privacy and synthetic data

The prototype uses only synthetic, mock, or safe simulated data.

It does not use:

- real customer names,
- real phone numbers,
- real account numbers,
- real balances,
- real provider credentials,
- PINs,
- OTPs,
- passwords,
- private keys,
- national identity numbers,
- or production transaction records.

Synthetic identifiers are used for:

- agents,
- providers,
- accounts,
- transactions,
- incidents,
- and cases.

The purpose of synthetic data is to allow controlled, repeatable evaluation without exposing real individuals or production systems.

---

## 7. Data minimization

Only information necessary for the demonstrated operational task is processed.

The core event model contains fields such as:

- timestamp,
- agent ID,
- provider ID,
- synthetic account ID,
- transaction type,
- amount,
- transaction status,
- area,
- and feed status.

The prototype does not collect unrelated personal attributes.

The design avoids using:

- religion,
- ethnicity,
- gender,
- political opinion,
- health information,
- or other protected personal characteristics.

The anomaly engine evaluates operational and transactional behaviour rather than unsupported demographic profiling.

---

## 8. Provider separation

The platform presents a combined operational view, but provider balances and authority remain separate.

The design distinguishes:

```text
Shared physical cash
bKash e-money
Nagad e-money
Rocket e-money
```

These values are displayed together for operational awareness but are not merged into one interchangeable financial resource.

The system does not imply that:

- bKash can control Nagad balances,
- Nagad can authorize Rocket actions,
- provider balances can be converted automatically,
- or one provider can inspect another provider’s confidential records.

Provider-specific cases remain provider-scoped.

For cross-provider patterns, the intended provider-facing output is:

```text
A related signal was observed in another provider context.
```

It should not reveal:

- the other provider’s raw balance,
- raw transaction identifiers,
- account-level details,
- confidential notes,
- or operational decisions.

---

## 9. Role-aware access and current prototype boundary

The prototype models several roles:

- multi-provider agent,
- field officer,
- provider operations,
- risk reviewer,
- management.

Different roles require different levels of detail.

### Agent

Should receive:

- own outlet balances,
- own liquidity pressure,
- a simple explanation,
- and a safe next step.

Should not receive:

- another agent’s data,
- raw risk evidence,
- or cross-provider confidential information.

### Field officer

Should receive:

- assigned or area-scoped agents,
- operational alerts,
- acknowledgement and coordination actions,
- and case updates.

### Provider operations

Should receive:

- provider-scoped balances and incidents,
- provider-scoped evidence,
- case ownership,
- and redacted cross-provider context.

### Risk reviewer

Should receive:

- detailed anomaly evidence,
- confidence,
- alternative explanations,
- and escalated case context.

### Management

Should receive:

- aggregate counts,
- area summaries,
- recurring problems,
- service readiness,
- and case backlog.

Management should not require raw account-level evidence.

### Current limitation

The prototype currently demonstrates role-aware explanations, provider-scoped workflow checks, and provider-aware redaction. It does not yet claim production-grade authentication or complete endpoint-level authorization.

A production deployment would require:

- authenticated identities,
- signed access tokens,
- verified role claims,
- provider and territory scope,
- database-level access controls,
- secure audit storage,
- and formal authorization review.

Demo role selectors or headers are for presentation only and must not be described as production security.

---

## 10. Data-quality uncertainty and safe fallback

The platform is designed not to produce strong conclusions from unreliable provider data.

The data-trust layer checks conditions such as:

- stale heartbeat,
- missing feed,
- delayed input,
- reported-versus-calculated balance conflict,
- and recovery after an outage.

When data is unreliable, the system may:

- lower confidence,
- return `INSUFFICIENT_DATA`,
- suppress a strong recommendation,
- create a data-quality warning,
- or recommend verification before action.

Example:

```text
Provider feed has been stale for 18 minutes.
Forecast confidence is reduced.
Verify the provider position before operational coordination.
```

This is safer than silently presenting a precise forecast based on incomplete information.

---

## 11. Explainability

Every high-impact alert should expose four elements:

1. **Situation** — what is happening.
2. **Evidence** — why the system thinks so.
3. **Uncertainty** — what may be incomplete or ambiguous.
4. **Safe next step** — what a human may consider doing.

Anomaly evidence is decomposed into understandable factors such as:

- transaction velocity,
- near-identical amounts,
- account concentration,
- abnormal failure rate,
- provider imbalance,
- balance conflict,
- or cross-provider relationship context.

The system should not expose only a single unexplained score.

A score is useful for prioritization, but the evidence behind it is necessary for review.

---

## 12. AI service boundary

AI is used only as a communication layer.

The OpenAI explanation service may:

- convert structured incident facts into readable language,
- adapt wording for agent, field officer, provider operations, risk reviewer, or management,
- and generate English, Bangla, or Banglish explanations.

AI does not:

- calculate balances,
- update the ledger,
- estimate liquidity,
- assign anomaly scores,
- create incidents,
- choose case owners,
- change case status,
- or authorize an action.

The deterministic backend remains the source of truth.

Before AI generation, the service prepares a minimized, provider-aware input.

After generation, the output is checked for:

- unsupported numbers,
- unsupported facts,
- provider-data leakage,
- unsafe financial actions,
- and inappropriate risk language.

When AI is unavailable or unsafe, the platform uses a deterministic template fallback.

This ensures that explanation availability is not dependent on an external model and that the live workflow remains stable.

---

## 13. AI data handling

The explanation service should receive only the minimum structured information required for the requested explanation.

It should not receive:

- real identities,
- credentials,
- full raw datasets,
- unnecessary transaction histories,
- or hidden ground-truth scenario labels.

Recommended AI input includes:

- incident category,
- affected provider,
- confidence,
- approved evidence summary,
- uncertainty,
- safe recommendation,
- case status,
- and viewer context.

The OpenAI API key must remain in environment configuration and must never be committed to the repository.

---

## 14. Hidden ground truth and evaluation integrity

Synthetic scenario labels are treated as hidden ground truth.

They are not available to:

- the ledger,
- the feed-trust engine,
- the liquidity engine,
- the anomaly detector,
- the fusion engine,
- the incident service,
- the case service,
- or the AI explanation service.

They are used only by the offline evaluator after public-event processing.

This prevents the detector from reading the answer and supports credible precision, recall, false-positive, and coverage measurements.

---

## 15. Financial safety

The stateful generator and ledger enforce financial consistency.

The design checks that:

- only successful transactions change balances,
- transaction effects follow the configured financial model,
- provider e-money remains provider-specific,
- shared cash remains agent-scoped,
- duplicate transaction IDs are rejected,
- and unexplained negative balances are prevented.

The prototype does not perform settlement, reconciliation, or wallet conversion.

Any future connection to real financial infrastructure would require:

- formal provider authorization,
- regulatory review,
- secure transaction signing,
- independent reconciliation,
- and strong operational controls.

Those capabilities are intentionally outside this prototype.

---

## 16. Case ownership and accountability

The workflow separates:

- alert receiver,
- responsible stakeholder,
- acknowledged-by user,
- case owner,
- escalation target,
- and final status.

These fields are not interchangeable.

For example:

```text
Receiver: Field Officer
Responsible stakeholder: Provider Operations
Acknowledged by: User A
Case owner: User B
Escalation target: Risk Reviewer
```

This separation improves accountability and makes it clear who was informed, who accepted responsibility, and who acted.

---

## 17. Auditability

Important workflow actions are recorded in the case history.

Examples include:

- case creation,
- acknowledgement,
- assignment,
- review start,
- notes,
- escalation,
- resolution,
- and closure.

Each audit event should record:

- timestamp,
- actor,
- action,
- previous state,
- new state,
- provider scope,
- note or reason,
- and resolution where applicable.

The prototype audit trail supports traceability but is currently not a tamper-proof production ledger.

A production design would use:

- persistent storage,
- append-only audit records,
- retention policies,
- restricted write access,
- and integrity verification.

---

## 18. Security controls

Current prototype controls include:

- synthetic data only,
- no production provider APIs,
- no real credentials,
- environment-based secrets,
- provider-aware redaction,
- input validation with typed schemas,
- enum-constrained domain values,
- safe case state transitions,
- and deterministic fallback behaviour.

The repository must not include:

- `.env`,
- real API keys,
- passwords,
- access tokens,
- or private certificates.

---

## 19. Fairness and unsupported profiling

The prototype does not infer risk from protected personal attributes.

Its indicators are based on observable operational factors such as:

- amounts,
- frequency,
- timing,
- provider flow,
- balance movement,
- transaction status,
- account concentration,
- and feed quality.

Even with these operational features, an unusual pattern may be legitimate. Therefore, the system does not treat its own score as a final judgment.

Future deployment would require:

- larger and more representative evaluation data,
- threshold calibration,
- subgroup testing where legally and ethically appropriate,
- false-positive monitoring,
- operator feedback,
- and periodic model or rule review.

---

## 20. Actions intentionally not performed

| Action | Performed by prototype? | Responsible-design reason |
|---|---|---|
| Show shared cash and provider balances | Yes | Core operational awareness |
| Forecast possible shortage | Yes | Advisory decision support |
| Explain anomaly evidence | Yes | Human-review support |
| Route an alert | Yes | Coordination |
| Assign a case | Yes | Accountability |
| Escalate a case | Yes | Safe handoff |
| Record notes and resolution | Yes | Auditability |
| Generate Bangla/Banglish explanation | Yes | Inclusive communication |
| Transfer liquidity | **No** | Requires provider authorization |
| Refill a wallet | **No** | Real financial action |
| Convert provider balances | **No** | Providers remain separate |
| Reverse a transaction | **No** | Outside advisory scope |
| Freeze funds | **No** | High-impact financial action |
| Block an account | **No** | Requires authorized investigation |
| Declare fraud | **No** | Anomaly is not proof |
| Accuse an agent or customer | **No** | Prevents unsupported harm |
| Access real provider systems | **No** | Prototype uses simulated integration |
| Collect PIN, OTP, or password | **No** | Sensitive credentials are prohibited |
| Expose another provider’s raw data | **No** | Provider confidentiality boundary |

---

## 21. Failure handling

The design prefers visible degradation over hidden failure.

Examples:

### Provider feed failure

```text
Action:
Lower trust score, reduce forecast confidence, show safe fallback.
```

### Conflicting balance input

```text
Action:
Create a data-quality warning and avoid strong operational advice.
```

### AI unavailable

```text
Action:
Use deterministic template fallback.
```

### Unsupported case transition

```text
Action:
Reject the request instead of silently changing state.
```

### Unknown agent or incident

```text
Action:
Return a clear not-found error.
```

### Invalid enum or malformed request

```text
Action:
Reject through schema validation.
```

---

## 22. Responsible limitations

The prototype has intentionally bounded limitations.

These limitations do not invalidate the system; they define where further engineering and governance are required.

### Synthetic-data limitation

The data is designed for controlled evaluation and does not reproduce every real-world provider pattern.

### Rule-calibration limitation

Thresholds are prototype values and require calibration against authorized historical data before any operational deployment.

### In-memory state limitation

Replay, incidents, and cases may be held in process memory. Production would require durable storage and recovery.

### Authentication limitation

The prototype does not claim production RBAC or secure identity federation.

### Scale limitation

Measured performance reflects the demonstrated synthetic volume and local test environment.

### Forecast limitation

Near-term forecasts use recent flow and simplified assumptions; they are not guarantees.

### Cross-provider limitation

Cross-provider relationships are simulated and privacy-safe. Real implementation would require formal agreements and strict access controls.

### AI limitation

Generated wording may vary. The facts and decisions remain deterministic and backend-controlled.

### Regulatory limitation

The prototype does not claim regulatory approval or production readiness.

---

## 23. Production evolution

A production-oriented version would add:

- authenticated users and provider-scoped RBAC,
- durable event and case storage,
- encrypted data at rest and in transit,
- append-only audit storage,
- formal retention and deletion policies,
- provider-specific ingestion adapters,
- consent and data-processing agreements,
- calibrated forecasting and anomaly thresholds,
- human-review feedback loops,
- monitoring for drift and false positives,
- independent security testing,
- legal and regulatory review,
- and operational playbooks approved by providers.

The architectural separation between ingestion, deterministic analytics, decision fusion, coordination, and explanation allows these upgrades without changing the core safety boundary.

---

## 24. Responsible-design checklist

| Requirement | Prototype response |
|---|---|
| Synthetic or safe data only | Yes |
| Shared cash and provider balances remain separate | Yes |
| Anomaly is not treated as fraud | Yes |
| Human review required | Yes |
| Evidence and uncertainty shown | Yes |
| Missing or conflicting data lowers confidence | Yes |
| Provider authority remains separate | Yes |
| No automatic financial action | Yes |
| No account blocking or fund freezing | Yes |
| No credential collection | Yes |
| Important case actions are traceable | Yes |
| AI is explanation-only | Yes |
| Deterministic fallback exists | Yes |
| Limitations documented | Yes |
| Production security not overstated | Yes |

---

## 25. Final responsible-design statement

The prototype is designed to make a complex multi-provider operating situation easier to understand without converting analytical insight into unauthorized action.

It connects:

```text
liquidity awareness
+ unusual-activity evidence
+ data-quality confidence
+ human ownership
+ traceable coordination
```

while intentionally preventing:

```text
automatic money movement
+ provider-boundary violations
+ unsupported accusations
+ final fraud decisions
+ exposure of real customer data
```

Its purpose is not to replace provider operations, risk, or compliance teams. Its purpose is to help them see the right evidence earlier, understand uncertainty, and coordinate a safer human response.
