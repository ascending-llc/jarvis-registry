# Retrieval is still the hardest part of the stack

> Production RAG in 2026: chunk strategies including late chunking, the embedding model landscape (OpenAI text-embedding-3, Cohere Embed v4, Voyage 3/4), re-rankers, the RAGAS eval framework, vector database selection, and when agentic RAG earns its seat.

*Pillar · Enterprise RAG · 17 minutes · Updated April 17, 2026 · Author Wenjia (Soraya) Zheng · Reviewed by Ziyi Tao*

!!! info "Originally published on Explore Agentic"

    The canonical version of this article lives at [www.exploreagentic.ai/enterprise-rag/](https://www.exploreagentic.ai/enterprise-rag/). Browse the full library of pillars, glossary entries, comparisons, and playbooks at [www.exploreagentic.ai](https://www.exploreagentic.ai/).


## Intro

The easy part of <strong>enterprise RAG</strong> is the chat interface. The hard part, still, in 2026, is everything upstream. Which documents to index, how to chunk, which embedding model and re-ranker to trust, and how to know when the system is wrong. Since Jina AI's September 2024 <em>late chunking</em> paper <a href="#cite-1" class="cite-ref">[1]</a> and Voyage 4's Mixture-of-Experts release in January 2026 <a href="#cite-2" class="cite-ref">[2]</a>, every part of this pipeline has moved. This pillar walks the whole thing and skips what every tutorial already covers.

Two editorial positions up front. Chunk strategy matters more than model choice on most corpora. And evaluation, specifically a <strong>named, versioned, nightly-run RAGAS-style eval set</strong> <a href="#cite-3" class="cite-ref">[3]</a>, is what separates RAG teams that ship from RAG teams that argue about which embedding model is <em>best</em> for six months. Everything below assumes the eval discipline is in place.

## TL;DR

- Chunk strategy matters more than model choice on most corpora. <em>Late chunking</em> (Jina AI, arXiv 2409.04701, Sep 7 2024) preserves context by embedding the full document first and chunking the token sequence afterwards <a href="#cite-1" class="cite-ref">[1]</a>.
- Embedding leaders as of April 2026: Voyage 4 (MoE, shared-space, Jan 2026) <a href="#cite-2" class="cite-ref">[2]</a>, Cohere Embed v4 (multimodal, 128K context) <a href="#cite-4" class="cite-ref">[4]</a>, OpenAI text-embedding-3-large with Matryoshka dimensions <a href="#cite-5" class="cite-ref">[5]</a>.
- Re-rankers remain the single highest-ROI component most teams skip. Cohere Rerank 3.5 (on Bedrock via Rerank API) is the default procurement path; improvements are largest on constrained and semi-structured queries <a href="#cite-6" class="cite-ref">[6]</a>.
- RAGAS is the open-source framework most teams standardise on. Metrics include Faithfulness, Context Precision, Context Recall, Response Relevancy, and an agentic suite (Tool Call F1, Agent Goal Accuracy) <a href="#cite-3" class="cite-ref">[3]</a>.
- Vector-database shortlist in April 2026: Pinecone (managed, SOC 2 Type II, ISO 27001, HIPAA-attested), Weaviate (hybrid-search native, SOC 2 Type II + HIPAA on AWS), Qdrant (Rust, fastest at 10M+ vectors under concurrency) <a href="#cite-7" class="cite-ref">[7]</a>.
- Agentic RAG earns its seat on multi-hop and ambiguous queries; it is a tax on simple lookups. See the [agentic RAG glossary entry](../glossary/agentic-rag.md).

## Stats

- **Sep 7, 2024** — Jina late chunking paper published *(arXiv 2409.04701. Context preserved by embedding whole document before splitting. Source [1].)*
- **Jan 2026** — Voyage 4 MoE release *(Shared embedding space across 4-large/4-lite; allows mixed-accuracy indexing without re-embedding. Source [2].)*
- **128K tokens** — Cohere Embed v4 context window *(Multimodal (text, image, mixed); shared embedding space. Source [4].)*
- **2-5x QPS** — Qdrant vs Weaviate at 10M+ vectors *(Measured on filtered queries at equivalent recall; 2025 third-party benchmarks. Source [7].)*

## 01. Chunk strategy: fixed, semantic, and late

In the public RAG post-mortems we have read (engineering blogs from LinkedIn, Databricks, Pinecone, LlamaIndex, and dozens of smaller teams) chunking is by a wide margin the most-cited root cause of poor retrieval. Splits that broke semantic units, splits that were too large and diluted relevance scores, splits that orphaned a reference from its antecedent. The fix is almost never a different embedding model. The fix is a better splitter.

Three families of chunking dominate in 2026. <strong>Fixed-size recursive-character</strong> is the default in most frameworks and works fine for pure prose. <strong>Semantic chunking</strong> (cluster adjacent sentences by cosine distance and cut at local maxima) improves retrieval on technical documents and is the upgrade most teams take first. <strong>Late chunking</strong>, published by Jina AI in September 2024 as arXiv 2409.04701 <a href="#cite-1" class="cite-ref">[1]</a>, is the interesting new primitive: embed the entire long document first with a long-context model, then split the token sequence into chunks and mean-pool each slice. Because every chunk embedding saw the full surrounding context, long-distance dependencies survive.

Format-aware splitting is still table stakes. PDFs with tables get table-aware chunking; code gets AST-aware chunking; markdown gets section-aware chunking. Generic recursive-character splitters work for prose and only prose. The practical recipe: semantic chunking for prose, late chunking when retrieval quality is the bottleneck and you can afford a long-context embedding model, format-specialised splitters everywhere else.

> Late chunking is the first genuinely new idea in RAG retrieval in two years. Everything else is tuning.

## 02. The embedding model landscape in April 2026

The embedding field consolidated around three vendors plus a strong open-source tail. <strong>Voyage AI</strong> shipped Voyage 4 on January 15, 2026. It is a Mixture-of-Experts architecture with a <em>shared embedding space</em> across 4-large, 4, and 4-lite, which means you can index documents with the large model and query with the lite model without re-embedding <a href="#cite-2" class="cite-ref">[2]</a>. Voyage 3-large already topped Cohere v4 and OpenAI 3-large on a number of MTEB tasks in January 2025 <a href="#cite-8" class="cite-ref">[8]</a>.

<strong>Cohere Embed v4</strong> is multimodal, embedding text, images, and mixed-modality content into the same vector space, with a 128,000-token context window, which is the feature that makes it competitive for enterprise document RAG on long PDFs and scanned tables <a href="#cite-4" class="cite-ref">[4]</a>. <strong>OpenAI text-embedding-3-large</strong> ($0.13 per million tokens) remains a common default, and the Matryoshka variable-dimension support introduced in 2024 is now standard in the category <a href="#cite-5" class="cite-ref">[5]</a>.

Open-source defaults for air-gapped deployments: BGE-M3 (multilingual, hybrid sparse+dense) and the Jina v3 family (long-context, late-chunking-ready). MTEB scores as of Q1 2026 cluster Voyage 4 > Cohere v4 > OpenAI 3-large > BGE-M3; enterprise workloads vary, but the ordering is consistent enough to use as a starting default.

*Embedding model leaders, April 2026. Prices are published list; context window is in tokens.*

| Model | Vendor / release | Context | Key differentiator |
| --- | --- | --- | --- |
| Voyage 4-large | Voyage AI, Jan 15 2026 | 32K | MoE architecture; shared embedding space with lite variants |
| Cohere Embed v4 | Cohere, 2025 | 128K | Multimodal text+image in one vector space |
| OpenAI text-embedding-3-large | OpenAI, 2024 | 8K | Matryoshka variable dimensions; $0.13/M tokens |
| Jina Embeddings v3 | Jina AI, 2024 | 8K | Late-chunking-ready; open source weights |
| BGE-M3 | BAAI, 2024 | 8K | Open source; hybrid sparse + dense; multilingual |

## 03. Re-rankers are the cheapest big improvement

The re-ranker is a second pass over retrieved candidates: take the top 50 from your vector search, re-score them with a cross-encoder, and keep the top 5. It is not glamorous. It is, empirically, the single highest-ROI component most teams skip.

<strong>Cohere Rerank 3.5</strong> is the default procurement path as of April 2026, available in Amazon Bedrock through the Rerank API and in Pinecone and Elasticsearch integrations <a href="#cite-6" class="cite-ref">[6]</a>. Cohere's own benchmarks show the largest improvements over Rerank v2 on constrained queries and semi-structured JSON; on generic prose benchmarks the lift is more modest. Rerank 3 Nimble is 3-5x faster than Rerank 3 with comparable accuracy on BEIR, worth evaluating when latency budget is tight.

Alternatives: <strong>Jina reranker-v2</strong> (open weights, competitive on short queries), <strong>mixedbread-ai/mxbai-rerank-large-v1</strong> (open source, strong on multilingual), and custom fine-tuned cross-encoders where the corpus is weird enough to justify the training investment. Latency cost for all commercial re-rankers is measured in a few hundred milliseconds per query, invisible to the end user on a conversational interface.

> Model choice is the conversation everyone wants to have. Chunking and re-ranking are the changes that actually move the benchmark.

## 04. Evaluation with RAGAS is the whole game

Teams ship or stall on evals. Teams with a named eval set (a specific corpus of queries with expected answers, run nightly) can answer <em>is this better?</em> with a number. Teams without one argue and wait for a product manager to decide.

The open-source framework most teams standardise on is <strong>RAGAS</strong> (arXiv 2309.15217). It offers reference-free metrics including Faithfulness, Context Precision, Context Recall, Context Entities Recall, Response Relevancy, Answer Accuracy, Factual Correctness, and Semantic Similarity, plus agentic metrics like Tool Call F1 and Agent Goal Accuracy <a href="#cite-3" class="cite-ref">[3]</a>. Faithfulness and Context Precision are the two that disagree most often in practice. A response can be fully grounded in retrieved context that was itself irrelevant. Track both or fool yourself.

Scale the eval set with the program. Minimum viable: 100 queries with graded answers. Mature: 2,000 queries covering every failure mode observed in production. Update weekly, run nightly, post the delta to a Slack channel. Every model change, prompt change, and chunker change ships with an eval-delta comment or it does not ship. LLM-as-judge saves annotation cost but drifts; calibrate quarterly against human graders.

- **Start with failures** — Seed the eval set with queries users complained about, not queries that worked. Bias towards the corpus edges.
- **Use four tiers, not binary** — Binary correct/incorrect loses signal. Four tiers (incorrect / partial / correct-with-caveat / correct) calibrates in two hours of annotator training.
- **Track Faithfulness and Context Precision separately** — A high-Faithfulness / low-Precision pair flags groundedness against the wrong evidence. Common early-stage failure.
- **LLM-as-judge is fine, once calibrated** — Calibrate quarterly against human graders. Model drift between GPT-4-class versions is real and can flip your sign.
- **Publish the delta** — Every change posts an eval-delta to the channel. No change ships silently; that rule is what separates real programs from theatre.

## 05. Vector databases in production, April 2026

The managed shortlist settled on three. <strong>Pinecone</strong> is the managed-simplicity option (SOC 2 Type II, ISO 27001, GDPR-aligned, with an external HIPAA attestation), and in BYOC mode clusters run inside the customer's own AWS, Azure, or GCP account for hard isolation <a href="#cite-7" class="cite-ref">[7]</a>. <strong>Weaviate</strong> is the flexible hybrid-search powerhouse; Weaviate Enterprise Cloud gained HIPAA compliance on AWS in 2025 and ships tenant-aware classes, lifecycle endpoints, and ACLs for multitenant deployments.

<strong>Qdrant</strong> is the performance-first option: Rust-native, SOC 2 Type II, with a markedly advanced filtering engine that lets complex metadata queries execute before the vector search. At 10M+ vectors with concurrent filtered queries, third-party benchmarks put Qdrant at 2-5x higher QPS than Weaviate on equivalent hardware at the same recall target. Open-source alternatives worth evaluating: Milvus (CNCF-graduated), pgvector for teams already on Postgres, and Chroma for prototyping.

Hybrid search, which combines vector similarity, keyword search, and metadata filters in one query, is the feature to verify in procurement. Weaviate and Qdrant include it by default; Pinecone added sparse-dense hybrid support through native integrations. If you need HIPAA, the shortlist narrows quickly; if you need on-prem isolation, Qdrant and Milvus are the common answers.

*Vector database enterprise posture, April 2026. Compliance claims verified against vendor trust pages.*

| Database | Model | Compliance | Strength |
| --- | --- | --- | --- |
| Pinecone | Fully managed; BYOC | SOC 2 II, ISO 27001, HIPAA attested, GDPR | Zero-ops managed; fast time-to-value |
| Weaviate | Managed + self-host | SOC 2 II, HIPAA on AWS (2025) | Hybrid search native; strong multitenancy |
| Qdrant | Managed + self-host | SOC 2 II; HIPAA-ready | Rust performance; best filtered-query QPS |
| Milvus | Self-host (CNCF) | Customer-configurable | Open source; GPU-accelerated at scale |
| pgvector | Postgres extension | Inherited from Postgres | Stay on existing database; simple ops |

## 06. When agentic RAG earns its seat

Agentic RAG is retrieval placed inside a planning loop. The agent decides what to retrieve, judges whether the retrieval was good enough, and retrieves again (or chooses a different index) if not. The cost is latency and token spend; the benefit is accuracy on multi-hop questions and ambiguous intent.

Add the agentic layer when the workload asks for it. Four triggers. Users ask follow-ups that depend on the previous retrieval. The correct answer requires joining two corpora. Intent is ambiguous and you would rather the system clarify than guess. Retrieval must decide among multiple typed sources (structured DB, unstructured document store, API). For simple lookups, agentic RAG is a tax: three extra seconds and a bigger token bill for no accuracy gain. The trigger list is short, which is the part most steering committees forget to check before shipping.

The governance implication: agentic RAG exposes new failure modes (retrieval loops, prompt injection via retrieved content) that OWASP LLM06 <em>Excessive Agency</em> and LLM08 <em>Vector and Embedding Weaknesses</em> specifically call out. Wire agentic RAG through the MCP gateway (see our [MCP](mcp.md) and [AI Governance](ai-governance.md) pillars) so tool calls are auditable and policy-governed.

> **Further reading on this site**: The [agentic RAG glossary entry](../glossary/agentic-rag.md) has the plain-language version with a diagram. For the protocol layer below, see our [MCP pillar](mcp.md). For eval and governance posture, see our [AI Governance pillar](ai-governance.md). Head-to-head coverage lives in the [MCP vs RAG comparison](../comparisons/mcp-vs-rag.md).

## FAQ

**Q: What is late chunking in RAG?**

Late chunking flips the order of two steps. Embed the whole long document first with a long-context model, then split the token sequence into chunks and mean-pool each slice. Jina AI published the technique on September 7, 2024 as arXiv 2409.04701 <a href="#cite-1" class="cite-ref">[1]</a>. Because every chunk embedding saw the full surrounding context, long-distance dependencies survive, the exact failure mode that sinks classic chunk-then-embed pipelines. The first genuinely new idea in retrieval in two years, which is why it was the one people argued about at conferences.

**Q: Which embedding model should I use for enterprise RAG in 2026?**

Three commercial defaults, plus open source for the air-gapped case. Voyage 4-large (January 15, 2026; MoE with a shared embedding space across the family) <a href="#cite-2" class="cite-ref">[2]</a> is the current MTEB leader. Cohere Embed v4 wins if you need multimodal (text plus image) at a 128K context window <a href="#cite-4" class="cite-ref">[4]</a>. OpenAI text-embedding-3-large remains the safe default at $0.13 per million tokens <a href="#cite-5" class="cite-ref">[5]</a>. For air-gapped: BGE-M3 or Jina v3. The ordering is consistent enough across enterprise corpora to use as a starting point, the part teams skip when they argue about model choice for six months.

**Q: Do I need a re-ranker?**

Almost certainly yes. It is the highest-ROI upgrade most RAG teams skip. Take the top 50 from your vector search, re-score with a cross-encoder, keep the top 5. Cohere Rerank 3.5 (via Amazon Bedrock's Rerank API) is the default procurement path; the lift is largest on constrained and semi-structured queries <a href="#cite-6" class="cite-ref">[6]</a>. Latency cost is a few hundred milliseconds per query, invisible on a conversational interface. The part RAG teams admit only privately: they spent a quarter arguing about embedding models before trying a re-ranker.

**Q: How should I evaluate a production RAG system?**

Use RAGAS as the baseline framework (arXiv 2309.15217) <a href="#cite-3" class="cite-ref">[3]</a>. Track four metrics at minimum. Faithfulness. Context Precision. Context Recall. Response Relevancy. Minimum-viable eval set is 100 graded queries. Mature looks more like 2,000, covering every observed failure mode. Run nightly. Post deltas to a public channel. No change ships without an eval-delta, the one rule that separates real programs from theatre.

**Q: Pinecone, Weaviate, or Qdrant?**

Three answers, one question each. Pinecone for zero-ops managed with SOC 2 Type II, ISO 27001, HIPAA-attested. Weaviate for hybrid-search native with strong multitenancy and HIPAA-on-AWS added in 2025. Qdrant for raw performance: Rust-native, 2 to 5x higher QPS than Weaviate at 10M+ vectors on filtered queries per third-party 2025 benchmarks <a href="#cite-7" class="cite-ref">[7]</a>. Milvus and pgvector are the open-source names worth a short-list. If you need HIPAA the list narrows fast. If you need on-prem isolation, the two open-source names are usually where teams land.

**Q: When does agentic RAG earn its seat?**

Four situations. Users ask follow-ups that depend on previous retrievals. The correct answer requires joining two corpora. Intent is ambiguous and you would rather the system clarify than guess. Retrieval must choose among multiple typed sources (structured DB, unstructured store, API). For simple lookups, agentic RAG is a tax: three extra seconds and a bigger token bill for no accuracy gain. The trap teams walk into: wiring agentic retrieval on top of a single store that never needed it. See the [agentic RAG glossary entry](../glossary/agentic-rag.md).

## Citations

1. **arXiv / Jina AI** — Late Chunking: Contextual Chunk Embeddings Using Long-Context Embedding Models — https://arxiv.org/abs/2409.04701 [accessed 2026-04-17] *(Published September 7, 2024. Authors Günther, Mohr, Wang, Xiao (Jina AI). Code: https://github.com/jina-ai/late-chunking.)* { #cite-1 }
2. **Voyage AI** — The Voyage 4 model family: shared embedding space with MoE architecture — https://blog.voyageai.com/2026/01/15/voyage-4/ [accessed 2026-04-17] *(January 15, 2026 release. First production embedding model with MoE; shared embedding space across 4-large/4/4-lite.)* { #cite-2 }
3. **RAGAS** — Ragas: Automated Evaluation of Retrieval Augmented Generation — https://docs.ragas.io/en/stable/ [accessed 2026-04-17] *(Framework documentation. Core paper arXiv 2309.15217. Metrics: Faithfulness, Context Precision/Recall, Response Relevancy, Tool Call F1, Agent Goal Accuracy.)* { #cite-3 }
4. **Cohere** — Cohere Embed v4 (multimodal, 128K-token context) — https://docs.cohere.com/docs/cohere-embed [accessed 2026-04-17] *(Cohere Embed v4 documentation. Text + image in shared embedding space; 128K token context window.)* { #cite-4 }
5. **OpenAI** — New embedding models (text-embedding-3-large / 3-small) — https://openai.com/index/new-embedding-models-and-api-updates/ [accessed 2026-04-17] *(OpenAI's text-embedding-3 family launch. Matryoshka variable dimensions; $0.13/M tokens for 3-large, $0.02/M for 3-small.)* { #cite-5 }
6. **AWS (Cohere)** — Cohere Rerank 3.5 is now available in Amazon Bedrock through Rerank API — https://aws.amazon.com/blogs/machine-learning/cohere-rerank-3-5-is-now-available-in-amazon-bedrock-through-rerank-api/ [accessed 2026-04-17] *(Cohere Rerank 3.5 availability via Bedrock Rerank API. Notable lift on constrained queries and semi-structured JSON.)* { #cite-6 }
7. **Xenoss** — Pinecone vs Qdrant vs Weaviate: enterprise vector database comparison — https://xenoss.io/blog/vector-database-comparison-pinecone-qdrant-weaviate [accessed 2026-04-17] *(Third-party 2025 comparison. SOC 2/ISO/HIPAA claims, BYOC, multitenancy, hybrid search, 2-5x Qdrant QPS on filtered queries.)* { #cite-7 }
8. **Voyage AI** — voyage-3-large: the new state-of-the-art general-purpose embedding model — https://blog.voyageai.com/2025/01/07/voyage-3-large/ [accessed 2026-04-17] *(January 7, 2025 release. Voyage 3-large topped Cohere v4 and OpenAI 3-large on a number of MTEB tasks.)* { #cite-8 }
9. **Jina AI** — Late Chunking in Long-Context Embedding Models (product notes) — https://jina.ai/news/late-chunking-in-long-context-embedding-models/ [accessed 2026-04-17] *(Jina AI late-chunking product notes; implementation available in jina-embeddings-v3 API with up to 8,192 tokens.)* { #cite-9 }


---

## Continue reading on Explore Agentic

This article is mirrored from [Explore Agentic](https://www.exploreagentic.ai/) — a curated reading list on agentic AI, the Model Context Protocol, AI governance, and enterprise RAG, published by ASCENDING Inc.

- **Read the original**: [www.exploreagentic.ai/enterprise-rag/](https://www.exploreagentic.ai/enterprise-rag/)
- **Library home**: [www.exploreagentic.ai](https://www.exploreagentic.ai/)
- **Pillars index**: [www.exploreagentic.ai/#pillars](https://www.exploreagentic.ai/#pillars)
