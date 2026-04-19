# Updated Pitch Docs

## The Core Story

Agnes is an AI supply chain manager for CPG procurement teams. It turns fragmented BOM, supplier, and procurement data into sourcing decisions that are cheaper, safer, and easier to defend.

The strongest positioning for judges is:

- Agnes does not just optimize for lowest cost.
- Agnes checks whether materials are substitutable in context.
- Agnes shows evidence, caveats, and tradeoffs before recommending action.
- Agnes explicitly avoids unsafe over-consolidation with an anti-monopoly guard.

That maps directly to the hackathon judging criteria:

- business relevance
- reasoning quality
- trustworthiness and hallucination control
- defensibility of the final sourcing proposal

## What We Actually Built

Ground this in the real product, not generic AI language:

- A Flask app with 4 surfaces: `Chat`, `Explorer`, `Insights`, and `Cube`.
- A structured analysis engine that profiles ingredients, detects substitution groups, scores consolidation, assesses risk, and generates recommendations.
- A reasoning chat agent using `gpt-4o-mini` with tool calls over the database.
- A data explorer for ingredients, suppliers, procurement, and graph relationships.
- A recommendation dashboard with evidence trails, confidence, caveats, and prioritization dimensions.

## Concrete Numbers To Use

Use these in the presentation so it feels real:

- 61 companies
- 149 finished goods
- 876 raw materials
- 357 unique ingredients
- 40 suppliers
- 8,127 procurement orders
- $1.52B historical procurement spend
- 117 substitution groups detected
- 125 consolidation opportunities identified
- 89 risk items flagged
- 54 total recommendations, 19 high-priority

## Best Message For Judges

One sentence:

Agnes helps procurement teams consolidate spend only when the substitute is functionally valid, commercially attractive, and still compliant.

Better expanded version:

Most sourcing tools stop at cost. Agnes reasons across BOM structure, supplier coverage, procurement history, quality/compliance signals, and substitution logic to recommend actions that buyers can actually trust and operationalize.

## Recommended Slide Structure

Keep it to 3 slides if the total speaking time is very short.

### Slide 1: Problem + Why It Matters

Headline:

`CPG companies are leaving money on the table because demand is fragmented and substitution is hard to prove.`

Say:

- The same ingredient is often bought by multiple companies and products with no shared visibility.
- That blocks volume leverage and creates hidden single-source risk.
- Consolidation is only useful if the substitute still meets quality and compliance requirements.

### Slide 2: Our Solution

Headline:

`Agnes turns fragmented procurement data into evidence-backed sourcing decisions.`

Show:

- Inputs: BOMs, supplier-product links, procurement history, supplier ratings, market benchmarks
- Reasoning layer: substitution detection, compliance-fit checks, risk detection, prioritization
- Outputs: consolidation proposals, risk mitigation actions, substitution standardization, cost optimization

Say:

- Agnes profiles each ingredient across companies.
- It finds variant and functional substitutes.
- It scores consolidation opportunities across leverage, evidence confidence, compliance fit, diversification, and switching feasibility.
- It produces recommendations with evidence trails and caveats.

### Slide 3: Why Our Approach Is Trustworthy

Headline:

`Agnes is designed to be defensible, not just impressive.`

Say:

- Every recommendation is tied to actual supplier, spend, and benchmark data.
- The UI exposes evidence, confidence, and caveats.
- The system includes an anti-monopoly guard, so it will downgrade recommendations that create single-source concentration risk.
- This is exactly what procurement teams need: not just answers, but justification.

## Best Real Examples To Mention

Use 2 or 3 examples only.

### Example 1: Safe consolidation

- `Beta Carotene`
- Used by 5 companies and bought from 4 suppliers
- Agnes recommends consolidating to `Prinova USA`
- Final score: `0.85`, grade: `safe_to_consolidate`
- Why this is strong: it aggregates volume while still leaving 3 backup suppliers in the network

### Example 2: Smart partial consolidation

- `Microcrystalline Cellulose`
- Used by 13 companies and sourced from 2 suppliers
- Agnes would like to consolidate, but downgrades the recommendation to `review_required`
- Why: full consolidation would create concentration risk
- This is one of the best proof points because it shows Agnes is not blindly minimizing suppliers

### Example 3: Risk mitigation

- `Maltodextrin`
- Single-source from `Ingredion`
- Affects 8 companies and 8 products
- Agnes recommends qualifying a second supplier and points to related alternatives

### Optional cost example

- `Vitamin A Palmitate`
- 22% supplier price spread
- Estimated savings: about `$610k`
- Good example if you want one clean cost-saving proof point

## Demo Strategy

The best demo is not the fanciest one. It is the one that shows reasoning, evidence, and business value in under 1 minute.

### Recommended Primary Demo

Use `Insights` and `Explorer` as the main flow. They are the most reliable and best aligned with judging.

Sequence:

1. Open `/agnes/` on `Recommendations`
2. Show a high-priority item like `Maltodextrin` or `Beta Carotene`
3. Switch to `Consolidation` and show `Microcrystalline Cellulose`
4. Point out the downgrade from full consolidation to partial consolidation because of concentration risk
5. Switch to `Substitutions` and show the `Vitamin D3` variant group across 22 companies
6. End in `Explorer -> Procurement` with one price-spread example like `Vitamin A Palmitate`

Final line:

`Agnes doesn’t just find a cheaper option. It tells you whether you should trust that option.`

### Optional Secondary Demo

If the OpenAI key is configured and stable, end with `Chat` for a single natural-language question such as:

`What are the risks and substitute options for maltodextrin?`

Only use chat if it is already working smoothly. Do not make the live or recorded demo depend on an external API if time is tight.

### What Not To Demo

- Do not center the demo on `Cube`
- Do not spend time on raw table browsing
- Do not claim live external enrichment if we are not showing it

The voice cube is visually cool, but the judges care more about sourcing logic, evidence quality, and trust.

## 1-Minute Demo Script

`Here Agnes has already analyzed the full network: 61 companies, 357 ingredients, 40 suppliers, and over 8,000 procurement orders.`

`On the recommendations tab, Agnes surfaces high-priority sourcing actions with evidence and caveats. For example, Beta Carotene can be safely consolidated because five companies are buying from four suppliers, but we still keep backup options in the network.`

`Now compare that with Microcrystalline Cellulose. Agnes sees strong consolidation leverage, but it downgrades the recommendation because full consolidation would create concentration risk. That anti-monopoly guard is important because procurement teams need resilience, not just lower prices.`

`Agnes also detects standardization opportunities, like Vitamin D3 variants used across 22 companies, and highlights cost spreads such as Vitamin A Palmitate, where a qualified supplier change could save roughly six hundred thousand dollars.`

`So the output is not just an answer. It is a sourcing proposal with evidence, tradeoffs, and trust built in.`

## 60-Second Spoken Pitch If Total Time Is Only 2 Minutes Including Video

If the format is truly `1 minute talking + 1 minute demo video`, use this:

`CPG companies overpay because the same ingredients are sourced in fragmented ways across products, plants, and even companies. But consolidation only works if the alternative is truly substitutable and still compliant.`

`We built Agnes, an AI supply chain manager that reasons across BOMs, supplier relationships, procurement history, quality signals, and market benchmarks to recommend sourcing actions teams can actually trust.`

`On our dataset, Agnes analyzed 357 ingredients and over 8,000 orders, identified 125 consolidation opportunities, 117 substitution groups, and 89 risk items, then turned them into evidence-backed recommendations.`

`What makes Agnes different is that it does not blindly chase the cheapest supplier. It exposes confidence, caveats, and even blocks unsafe over-consolidation with an anti-monopoly guard.`

`So instead of just saying “buy this cheaper material,” Agnes tells procurement teams what to consolidate, what to standardize, what to dual-source, and why.`

## Architecture / Model Choices To Mention

Keep this short in the presentation:

- Backend: Python + Flask
- Data layer: SQLite + SQLAlchemy / SQL queries
- LLM layer: `gpt-4o-mini` for tool-based natural-language analysis
- Analysis layer: custom scoring engine for substitution, consolidation, risk, and recommendations
- Frontend: lightweight HTML/CSS/JS dashboards for explainability, not flashy UI

Good phrasing:

`We intentionally kept the stack simple and focused our effort on decision quality, evidence handling, and explainability.`

## How To Talk About External Enrichment

Be careful here.

Say this:

- The current prototype is strongest on internal-data reasoning.
- The architecture is ready for external enrichment such as certifications, regulatory references, supplier pages, and label evidence.
- That is the next step to strengthen compliance inference further.

Do not say:

- that we already have robust live web enrichment if we are not demonstrating it
- that compliance is fully automated end-to-end

## Best Closing

Pick one:

- `Agnes turns fragmented procurement data into sourcing decisions teams can defend.`
- `The value is not just cheaper sourcing. It is trustworthy sourcing.`
- `In supply chain, the best recommendation is the one a buyer can actually act on.`

## Presenter Notes

- Focus on business value first, not technical detail first.
- Use the anti-monopoly guard as the memorable differentiator.
- Mention evidence, caveats, and confidence at least twice.
- Keep the story around trustworthiness, because that is where this project is strongest.
- If asked about polish, say the UI is intentionally lightweight because the focus is decision quality.

