# Embeddings Configuration

Flexible, vendor-agnostic embeddings generation for MCP Gateway Registry's semantic search functionality.

## Overview

The MCP Gateway Registry provides semantic search capabilities across MCP servers, tools, and AI agents. You can choose from three embedding provider options to power this search:

1. **Sentence Transformers** (Default) - Local models
2. **OpenAI** - Cloud embeddings via API
3. **Any LiteLLM-supported provider** - Amazon Bedrock Titan, Cohere, and 100+ other models

Switch between providers with simple configuration changes - no code modifications required.

## Features

- **Vendor-agnostic**: Switch between embeddings providers with configuration changes
- **Local & Cloud Support**: Use local models or cloud APIs (OpenAI, Cohere, Amazon Bedrock, etc.)
- **Backward Compatible**: Works seamlessly with existing FAISS indices
- **Easy Configuration**: Simple environment variable setup
- **Extensible**: Easy to add new providers
- **Production-Ready**: Terraform support for AWS deployments

## Quick Start

### Option 1: Sentence Transformers (Default)

Local embedding models that run on your infrastructure.

```bash
# In .env
EMBEDDINGS_PROVIDER=sentence-transformers
EMBEDDINGS_MODEL_NAME=all-MiniLM-L6-v2
EMBEDDINGS_MODEL_DIMENSIONS=384
```

**Characteristics:**
- Runs locally on your infrastructure
- No API costs
- No external network calls required
- Requires CPU/GPU resources
- Model files stored locally
- Data stays within your infrastructure

### Option 2: OpenAI

Cloud-based embedding service via OpenAI API.

```bash
# In .env
EMBEDDINGS_PROVIDER=litellm
EMBEDDINGS_MODEL_NAME=openai/text-embedding-ada-002
EMBEDDINGS_MODEL_DIMENSIONS=1536
EMBEDDINGS_API_KEY=sk-your-openai-api-key
```

**Characteristics:**
- Cloud-based service
- Requires API key
- API costs per 1K tokens
- No local compute resources needed
- Network dependency
- Data sent to OpenAI

### Option 3: Amazon Bedrock Titan

Cloud-based embedding service via AWS Bedrock.

```bash
# In .env
EMBEDDINGS_PROVIDER=litellm
EMBEDDINGS_MODEL_NAME=bedrock/amazon.titan-embed-text-v1
EMBEDDINGS_MODEL_DIMENSIONS=1536
EMBEDDINGS_AWS_REGION=us-east-1
# No API key needed - uses IAM
```

**Characteristics:**
- Cloud-based service
- Uses IAM authentication (no API key required)
- Integrates with AWS security model
- API costs apply
- Requires AWS credentials
- Available in select AWS regions

### Option 4: Azure OpenAI

Cloud-based embedding service via Azure-hosted OpenAI models.

```bash
# In .env
VECTOR_STORE_TYPE=weaviate
EMBEDDINGS_PROVIDER=azure_openai
AZURE_OPENAI_API_KEY=<AZURE_OPENAI_API_KEY>
AZURE_OPENAI_ENDPOINT=https://example.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-02-01
AZURE_OPENAI_DEPLOYMENT_NAME=<AZURE_OPENAI_DEPLOYMENT_NAME>
AZURE_OPENAI_MODEL=<AZURE_OPENAI_MODEL>
```

**Characteristics:**
- Cloud-based service via Azure
- Requires Azure OpenAI resource and API key
- API costs apply (Azure pricing)
- Integrates with Azure security model
- Data stays within Azure regions
- Supports private endpoints and VNet integration
- Enterprise-grade SLA and compliance

## Configuration

### Environment Variables

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `EMBEDDINGS_PROVIDER` | Provider type: `sentence-transformers` or `litellm` | `sentence-transformers` | No |
| `EMBEDDING_PROVIDER` | New format: `openai`, `aws_bedrock`, `azure_openai` | `aws_bedrock` | For new config |
| `EMBEDDINGS_MODEL_NAME` | Model identifier | `all-MiniLM-L6-v2` | Yes |
| `EMBEDDINGS_MODEL_DIMENSIONS` | Embedding dimension | `384` | Yes |
| `EMBEDDINGS_API_KEY` | API key for cloud provider (OpenAI, Cohere, etc.) | - | For cloud* |
| `EMBEDDINGS_API_BASE` | Custom API endpoint (LiteLLM only) | - | No |
| `EMBEDDINGS_AWS_REGION` | AWS region for Bedrock (LiteLLM only) | - | For Bedrock |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | - | For Azure OpenAI |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL | - | For Azure OpenAI |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI API version | `2024-02-01` | No |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | Azure OpenAI deployment name | - | For Azure OpenAI |
| `AZURE_OPENAI_MODEL` | Azure OpenAI model name | `text-embedding-3-small` | No |

*Not required for AWS Bedrock - use standard AWS credential chain (IAM roles, environment variables, ~/.aws/credentials)

### Terraform Configuration

For AWS ECS deployments, configure embeddings in your `terraform.tfvars`:

#### Using Sentence Transformers (Default)

```hcl
# Local embeddings - no additional configuration needed
# Uses defaults: sentence-transformers with all-MiniLM-L6-v2
```

#### Using OpenAI

```hcl
embeddings_provider         = "litellm"
embeddings_model_name       = "openai/text-embedding-ada-002"
embeddings_model_dimensions = 1536
embeddings_api_key          = "sk-proj-YOUR-OPENAI-API-KEY"
```

#### Using Amazon Bedrock

```hcl
embeddings_provider         = "litellm"
embeddings_model_name       = "bedrock/amazon.titan-embed-text-v1"
embeddings_model_dimensions = 1536
embeddings_aws_region       = "us-east-1"
embeddings_api_key          = ""  # Empty for Bedrock (uses IAM)
```

#### Using Azure OpenAI

```hcl
vector_store_type                 = "weaviate"
embedding_provider                = "azure_openai"
azure_openai_api_key              = <AZURE_OPENAI_API_KEY>
azure_openai_endpoint             = https://example.openai.azure.com
azure_openai_api_version          = "2024-02-01"
azure_openai_deployment_name      = <AZURE_OPENAI_DEPLOYMENT_NAME>
azure_openai_model                = <AZURE_OPENAI_MODEL>
```

See [terraform/aws-ecs/terraform.tfvars.example](../terraform/aws-ecs/terraform.tfvars.example) for complete examples.

## Supported Models

### Sentence Transformers (Local)

| Model | Dimensions | Description |
|-------|------------|-------------|
| `all-MiniLM-L6-v2` | 384 | Fast, lightweight (default) |
| `all-mpnet-base-v2` | 768 | High quality |
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | Multilingual |

Any model from [Hugging Face sentence-transformers](https://huggingface.co/models?library=sentence-transformers) is supported.

### LiteLLM (Cloud-based)

LiteLLM supports 100+ embedding models from various providers:

#### OpenAI
- `openai/text-embedding-3-small` (1536 dimensions)
- `openai/text-embedding-3-large` (3072 dimensions)
- `openai/text-embedding-ada-002` (1536 dimensions)

#### Cohere
- `cohere/embed-english-v3.0` (1024 dimensions)
- `cohere/embed-multilingual-v3.0` (1024 dimensions)

#### Amazon Bedrock
- `bedrock/amazon.titan-embed-text-v1` (1536 dimensions)
- `bedrock/cohere.embed-english-v3` (1024 dimensions)
- `bedrock/cohere.embed-multilingual-v3` (1024 dimensions)

#### Azure OpenAI
- `text-embedding-3-small` (1536 dimensions)
- `text-embedding-3-large` (3072 dimensions)
- `text-embedding-ada-002` (1536 dimensions)

**Note:** When using Azure OpenAI, you must specify the deployment name (not the model name) in your Azure OpenAI resource.

#### Other Providers
- Azure OpenAI
- Anthropic (Claude)
- Google Vertex AI
- Hugging Face Inference API
- And 100+ more via [LiteLLM](https://docs.litellm.ai/docs/embedding/supported_embedding)

## Migration Between Providers

### Switching Providers

When you switch embedding providers or models with different dimensions, the registry automatically:

1. Detects dimension mismatch
2. Rebuilds the FAISS index
3. Regenerates embeddings for all registered items

Example logs when switching from sentence-transformers (384) to OpenAI (1536):

```
WARNING: Embedding dimension mismatch detected
  Expected: 384 (from existing index)
  Got: 1536 (from current model)
Rebuilding FAISS index with new dimensions...
Regenerating embeddings for all items...
Index rebuild complete
```

### No Code Changes Required

Just update your environment variables or Terraform configuration:

```bash
# From
EMBEDDINGS_PROVIDER=sentence-transformers
EMBEDDINGS_MODEL_NAME=all-MiniLM-L6-v2
EMBEDDINGS_MODEL_DIMENSIONS=384

# To
EMBEDDINGS_PROVIDER=litellm
EMBEDDINGS_MODEL_NAME=openai/text-embedding-ada-002
EMBEDDINGS_MODEL_DIMENSIONS=1536
EMBEDDINGS_API_KEY=sk-your-key
```

Restart the service and the index will be automatically rebuilt.

## AWS Bedrock Setup

### IAM Permissions

For Amazon Bedrock embeddings, ensure your ECS task role has the following permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel"
      ],
      "Resource": [
        "arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v1"
      ]
    }
  ]
}
```

### Authentication Methods

**IAM Roles (Recommended for ECS/EC2/EKS)**
```bash
# No additional configuration needed
# ECS task, EC2 instance, or EKS pod automatically uses attached IAM role
```

## Architecture

### Embeddings Module Design

```
EmbeddingsClient (Abstract Base Class)
├── SentenceTransformersClient (Local models)
└── LiteLLMClient (Cloud APIs via LiteLLM)
```

### Integration with FAISS Search

The embeddings module integrates seamlessly with the FAISS search service:

```python
# In registry/search/service.py
from registry.embeddings import create_embeddings_client

class FaissService:
    async def _load_embedding_model(self):
        self.embedding_model = create_embeddings_client(
            provider=settings.embeddings_provider,
            model_name=settings.embeddings_model_name,
            api_key=settings.embeddings_api_key,
            aws_region=settings.embeddings_aws_region,
            embedding_dimension=settings.embeddings_model_dimensions,
        )
```

## Performance Considerations

### Local Models (Sentence Transformers)
- Runs on your infrastructure (CPU/GPU)
- No external API calls
- No per-request costs
- Model files stored locally
- Network-independent operation

### Cloud APIs (LiteLLM)
- Runs on provider infrastructure
- Requires network connectivity
- API costs apply (varies by provider)
- No local compute requirements
- Data transmitted to provider

## Troubleshooting

### LiteLLM Not Installed

```
RuntimeError: LiteLLM is not installed. Install it with: uv add litellm
```

**Solution:**
```bash
uv add litellm
```

### Dimension Mismatch

```
WARNING: Embedding dimension mismatch: expected 384, got 1536
```

**Solution:** Update `EMBEDDINGS_MODEL_DIMENSIONS` to match your model's actual output dimension. The system will automatically rebuild the index.

### API Authentication Errors

**OpenAI:**
```bash
# Verify API key is set correctly
echo $EMBEDDINGS_API_KEY
# Should start with sk-
```

**Bedrock:**
```bash
# Verify AWS credentials
aws sts get-caller-identity

# Check Bedrock access
aws bedrock list-foundation-models --region us-east-1
```

### Missing IAM Permissions

If using AWS ECS and Bedrock, ensure the task execution role has access to the embeddings API key secret:

```bash
# Check IAM policy in terraform/aws-ecs/modules/mcp-gateway/iam.tf
# Should include: aws_secretsmanager_secret.embeddings_api_key.arn
```

## API Reference

### Factory Function

```python
from registry.embeddings import create_embeddings_client

client = create_embeddings_client(
    provider: str,                    # "sentence-transformers" or "litellm"
    model_name: str,                  # Model identifier
    api_key: Optional[str] = None,    # API key (litellm only)
    aws_region: Optional[str] = None, # AWS region (Bedrock only)
    embedding_dimension: Optional[int] = None,
)
```

### Client Methods

**Generate Embeddings:**
```python
embeddings = client.encode(["text1", "text2"])
# Returns: numpy array of shape (n_texts, embedding_dim)
```

**Get Dimension:**
```python
dim = client.get_embedding_dimension()
# Returns: int (e.g., 384, 1536)
```

## Best Practices

1. Choose the provider that matches your deployment requirements
2. Consider IAM authentication if deploying on AWS
3. Monitor costs when using cloud APIs - implement caching if needed
4. Keep dimension consistent - changing models requires index rebuild
5. Test search results after switching providers to ensure they meet your requirements

## Further Reading

- [LiteLLM Documentation](https://docs.litellm.ai/docs/)
- [OpenAI Embeddings Guide](https://platform.openai.com/docs/guides/embeddings)
- [Amazon Bedrock Embeddings](https://docs.aws.amazon.com/bedrock/latest/userguide/embeddings.html)
- [Sentence Transformers Models](https://www.sbert.net/docs/pretrained_models.html)
- [FAISS Search Implementation](../registry/search/service.py)

## Contributing

To add a new embeddings provider:

1. Create a new client class inheriting from `EmbeddingsClient`
2. Implement `encode()` and `get_embedding_dimension()` methods
3. Update `create_embeddings_client()` factory function
4. Add configuration options to `registry/core/config.py`
5. Update this documentation

## License

Apache 2.0 - See [LICENSE](../LICENSE) file for details
