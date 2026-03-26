# Embeddings Configuration

This project has two different embedding configurations, and which one applies depends entirely on `TOOL_DISCOVERY_MODE`.

## Overview

Embeddings are used for semantic search in the registry.

Depending on deployment mode, semantic search is handled in one of two ways:

- `embedded`: local FAISS index with a local `sentence-transformers` model
- `external`: external vector backend with a configured embedding provider such as `aws_bedrock` or `openai`

## Which Settings Apply?

| `TOOL_DISCOVERY_MODE` | Service Used | Relevant Variables |
|---|---|---|
| `embedded` | `EmbeddedFaissService` | `LOCAL_EMBEDDINGS_MODEL_NAME`, `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS` |
| `external` | External vector backend | `VECTOR_STORE_TYPE`, `EMBEDDING_PROVIDER`, and provider-specific settings such as `EMBEDDING_MODEL`, `AWS_REGION`, `OPENAI_API_KEY`, `OPENAI_MODEL` |

In the current container setup, the default is `TOOL_DISCOVERY_MODE=external`.

## Configuration

### Embedded Mode

Use this configuration when `TOOL_DISCOVERY_MODE=embedded`.

Required variables:

- `TOOL_DISCOVERY_MODE=embedded`
- `LOCAL_EMBEDDINGS_MODEL_NAME`
- `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS`

Example:

```bash
TOOL_DISCOVERY_MODE=embedded
LOCAL_EMBEDDINGS_MODEL_NAME=all-MiniLM-L6-v2
LOCAL_EMBEDDINGS_MODEL_DIMENSIONS=384
```

### External Mode

Use this configuration when `TOOL_DISCOVERY_MODE=external`.

Common variables:

- `TOOL_DISCOVERY_MODE=external`
- `VECTOR_STORE_TYPE`
- `EMBEDDING_PROVIDER`

#### External Mode with `aws_bedrock`

Required variables:

- `VECTOR_STORE_TYPE=weaviate`
- `EMBEDDING_PROVIDER=aws_bedrock`
- `EMBEDDING_MODEL`
- `AWS_REGION`

Optional variables:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_SESSION_TOKEN`

Example:

```bash
TOOL_DISCOVERY_MODE=external
VECTOR_STORE_TYPE=weaviate
EMBEDDING_PROVIDER=aws_bedrock
EMBEDDING_MODEL=your_bedrock_embedding_model_id
AWS_REGION=us-east-1
```

#### External Mode with `openai`

Required variables:

- `VECTOR_STORE_TYPE=weaviate`
- `EMBEDDING_PROVIDER=openai`
- `OPENAI_API_KEY`

Optional variables:

- `OPENAI_MODEL`

Example:

```bash
TOOL_DISCOVERY_MODE=external
VECTOR_STORE_TYPE=weaviate
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=text-embedding-3-small
```

## Embedded FAISS Mode

When `TOOL_DISCOVERY_MODE=embedded`, the registry loads `EmbeddedFaissService` and uses a local `sentence-transformers` model.
In this mode, local embeddings are file-based models from Hugging Face, not OpenAI or Bedrock API calls.

Relevant settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `LOCAL_EMBEDDINGS_MODEL_NAME` | Hugging Face sentence-transformers model name | `all-MiniLM-L6-v2` |
| `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS` | Expected embedding dimension for the FAISS index | `384` |

Required variables when `TOOL_DISCOVERY_MODE=embedded`:

- `LOCAL_EMBEDDINGS_MODEL_NAME`
- `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS`

Notes:

- The model is loaded either from `registry/models/<model-name>` in local development or from `/app/registry/models/<model-name>` in the container.
- If the model is not already present, `sentence-transformers` downloads it from Hugging Face Hub.
- The Hugging Face cache is stored next to the local models directory under `.cache`.
- No local embedding provider, API key, or AWS region setting is used in this mode.

### Common Embedded Models

| Model | Typical Dimensions | Notes |
|---|---:|---|
| `all-MiniLM-L6-v2` | 384 | Default, lightweight |
| `all-mpnet-base-v2` | 768 | Higher quality, larger model |
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | Multilingual use cases |

## External Vector Search Mode

When `TOOL_DISCOVERY_MODE=external`, the registry does not load `EmbeddedFaissService`.
Semantic vectorization is handled by the external vector backend configuration instead.

Relevant settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `VECTOR_STORE_TYPE` | Vector store backend | `weaviate` |
| `EMBEDDING_PROVIDER` | Embedding provider for the external vector backend | `aws_bedrock` |
| `EMBEDDING_MODEL` | Embedding model ID used by the provider | provider-specific default |
| `AWS_REGION` | AWS region for Bedrock embeddings | `us-east-1` |
| `OPENAI_API_KEY` | OpenAI API key when `EMBEDDING_PROVIDER=openai` | - |
| `OPENAI_MODEL` | OpenAI embedding model name | `text-embedding-3-small` |

Notes:

- In AWS environments, IAM role or the default AWS credential chain is preferred over hardcoded credentials.
- `EMBEDDING_MODEL` is the model identifier passed through pydantic for Bedrock embeddings.
- If `OPENAI_MODEL` is not provided, the default is `text-embedding-3-small`.
- `EMBEDDING_MODEL` is not used for the OpenAI embedding path in the current configuration model.

### Common External Models

| Provider | Model | Typical Dimensions |
|---|---|---:|
| `aws_bedrock` | `bedrock-model-v2` | 1024 |
| `aws_bedrock` | `bedrock-model-v1` | 1536 |
| `openai` | `text-embedding-3-small` | 1536 |
| `openai` | `text-embedding-3-large` | 3072 |

## Behavior When Settings Change

### Embedded Mode

If you change `LOCAL_EMBEDDINGS_MODEL_NAME` or `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS`, make sure the configured dimension matches the model's real output dimension.

If the configured dimension does not match the existing FAISS index dimension, the registry re-initializes the FAISS index.

In practice, this means:

- switching to a model with a different output dimension requires rebuilding the local FAISS index
- using the wrong dimension value can cause the system to discard the old index and start a new one

### External Mode

If you change `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, or provider-specific settings, semantic vectorization behavior changes in the external vector backend path.

This does not load `EmbeddedFaissService`, so local FAISS-specific settings do not apply.

## Operational Notes

- `EMBEDDING_MODEL` is the pydantic-backed setting used for external embedding model selection. `BEDROCK_MODEL` is no longer the source of truth for registry vectorization.
- Because the current container setup defaults to `TOOL_DISCOVERY_MODE=external`, local FAISS embedding settings usually do not need to be added to AWS Secrets Manager.
- If you change the local sentence-transformers model, make sure `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS` matches the model output dimension, or the FAISS index will be rebuilt.
- `LOCAL_EMBEDDINGS_MODEL_NAME` and `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS` only apply when `TOOL_DISCOVERY_MODE=embedded`.
- `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `AWS_REGION`, `OPENAI_API_KEY`, and `OPENAI_MODEL` only apply when `TOOL_DISCOVERY_MODE=external`.

## Troubleshooting

### Embedded Mode: Model Downloads or Cache Location

- If the local model is not already present, `sentence-transformers` downloads it from Hugging Face Hub.
- The cache directory is stored next to the local models directory under `.cache`.
- In container mode, models live under `/app/registry/models`.

### Embedded Mode: Dimension Mismatch

Symptoms:

- FAISS index is re-initialized on startup
- semantic search index appears to be rebuilt after changing the model

What to check:

- `LOCAL_EMBEDDINGS_MODEL_NAME`
- `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS`
- whether the configured dimension matches the selected sentence-transformers model

### External Mode: Bedrock Authentication

What to check:

- `EMBEDDING_PROVIDER=aws_bedrock`
- `EMBEDDING_MODEL`
- `AWS_REGION`
- IAM role or AWS credential chain

In AWS environments, IAM-based authentication is preferred over hardcoded credentials.

### External Mode: OpenAI Authentication

What to check:

- `EMBEDDING_PROVIDER=openai`
- `OPENAI_API_KEY`
- `OPENAI_MODEL` if you are overriding the default

## API Reference

This section summarizes the main code-level configuration entry points used by the registry embeddings flow.

### `Settings`

The registry application reads embedding-related settings from `registry.core.config.Settings`.

Important fields:

- `tool_discovery_mode`
- `local_embeddings_model_name`
- `local_embeddings_model_dimensions`
- `vector_store_type`
- `embedding_provider`
- `embedding_model`
- `aws_region`
- `openai_api_key`
- `openai_model`

### `VectorConfig`

External vectorization settings are passed into the shared `registry_pkgs.core.config.VectorConfig` model.

Important fields:

- `vector_store_type`
- `embedding_provider`
- `embedding_model`
- `aws_region`
- `openai_api_key`
- `openai_model`

### `RegistryContainer.vector_service`

The registry selects the vector search implementation in `RegistryContainer.vector_service`:

- when `tool_discovery_mode == "external"`, it returns `ExternalVectorSearchService`
- otherwise, it returns `EmbeddedFaissService`

This is the main switch that determines whether local FAISS settings or external embedding provider settings are used.

### `EmbeddedFaissService`

`EmbeddedFaissService` is used only in embedded mode.

Relevant behavior:

- loads a local `sentence-transformers` model using `LOCAL_EMBEDDINGS_MODEL_NAME`
- validates or initializes the FAISS index using `LOCAL_EMBEDDINGS_MODEL_DIMENSIONS`
- stores model files under the local embeddings model directory
- stores Hugging Face cache under the adjacent `.cache` directory

### `BedrockEmbeddingConfig`

For `EMBEDDING_PROVIDER=aws_bedrock`, the external embedding model configuration is derived from:

- `embedding_model`
- `aws_region`
- optional AWS credentials

The model value comes from `EMBEDDING_MODEL`, not `BEDROCK_MODEL`.

### `OpenAIEmbeddingConfig`

For `EMBEDDING_PROVIDER=openai`, the external embedding model configuration is derived from:

- `openai_api_key`
- `openai_model`
