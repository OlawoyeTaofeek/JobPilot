"""Turn skill names as written ("PyTorch", "Amazon Web Services (AWS)") into one canonical name.

Canonical names are what you GROUP BY for "top skills this week", so
"PyTorch", "pytorch" and "Py-Torch" must all become "pytorch".

How it works:
    SKILLS maps  category -> {canonical name: [aliases as people write them]}
    At import, every alias AND the canonical name are passed through _clean() and stored in
    ALIASES (cleaned text -> canonical). So you can write aliases naturally ("CI/CD",
    "Scikit-Learn") and never worry about the cleaned form.

Unknown skills are kept as their cleaned text, so nothing is lost. Grow SKILLS when you
see a new spelling in real extractions:
    SELECT skill, count(*) FROM posting_skills
    WHERE skill NOT IN (...known...) GROUP BY 1 ORDER BY 2 DESC;
"""

from __future__ import annotations

import re

MAX_SKILL_LEN = 80  # matches posting_skills.skill String(80)

# fmt: off
SKILLS: dict[str, dict[str, list[str]]] = {
    # ------------------------------------------------------------------ languages
    "programming_language": {
        "python": ["python3", "python 3", "py"],
        "sql": ["structured query language", "ansi sql", "t-sql", "tsql", "pl/sql", "plsql", "sql programming"],
        "r": ["r programming", "r language", "rstats"],
        "scala": [],
        "java": [],
        "javascript": ["js", "ecmascript"],
        "typescript": ["ts"],
        "go": ["golang"],
        "rust": [],
        "c++": ["cpp", "c plus plus"],
        "c#": ["csharp", "c sharp"],
        "c": [],
        "julia": [],
        "bash": ["shell", "shell scripting", "bash scripting", "unix shell", "zsh"],
        "powershell": [],
        "matlab": [],
        "sas": ["sas programming", "sas base"],
        "stata": [],
        "spss": ["ibm spss"],
        "vba": ["excel vba", "visual basic"],
        "dax": ["data analysis expressions"],
        "m language": ["power query m", "m code"],
        "lookml": [],
        "cuda": ["cuda programming"],
        "kotlin": [],
        "swift": [],
        "php": [],
        "ruby": [],
    },

    # ------------------------------------------------------------------ core ML / DS
    "ml_concept": {
        "machine learning": ["ml", "machine learning models", "machine learning algorithms"],
        "deep learning": ["dl", "deep neural networks", "neural networks", "neural nets", "dnn"],
        "statistics": ["statistical analysis", "statistical modeling", "statistical modelling", "stats", "applied statistics"],
        "probability": [],
        "linear algebra": [],
        "calculus": [],
        "optimization": ["optimisation", "mathematical optimization", "operations research"],
        "supervised learning": [],
        "unsupervised learning": ["clustering"],
        "reinforcement learning": ["rl"],
        "rlhf": ["reinforcement learning from human feedback"],
        "nlp": ["natural language processing", "text mining", "text analytics"],
        "computer vision": ["cv", "image processing", "image recognition"],
        "speech recognition": ["asr", "automatic speech recognition", "speech to text"],
        "time series": ["time series analysis", "time series forecasting", "forecasting"],
        "recommender systems": ["recommendation systems", "recommendation engines", "recsys"],
        "anomaly detection": ["outlier detection", "fraud detection"],
        "feature engineering": ["feature selection"],
        "model evaluation": ["model validation", "cross validation"],
        "hyperparameter tuning": ["hyperparameter optimization", "hpo"],
        "a/b testing": ["ab testing", "a b testing", "split testing", "online experimentation"],
        "experimentation": ["experiment design", "experimental design", "design of experiments", "doe"],
        "causal inference": ["causal analysis", "uplift modeling", "uplift modelling"],
        "bayesian statistics": ["bayesian methods", "bayesian inference", "bayesian modeling"],
        "regression": ["linear regression", "logistic regression", "regression analysis"],
        "classification": [],
        "gradient boosting": ["gbm", "boosting"],
        "random forest": ["random forests"],
        "transformers": ["transformer models", "transformer architecture"],
        "cnn": ["convolutional neural networks", "convolutional neural network"],
        "rnn": ["recurrent neural networks", "lstm"],
        "gans": ["gan", "generative adversarial networks"],
        "diffusion models": ["stable diffusion", "diffusion"],
        "graph neural networks": ["gnn", "gnns"],
        "data mining": [],
        "predictive modeling": ["predictive modelling", "predictive analytics"],
        "segmentation": ["customer segmentation"],
        "survival analysis": [],
    },

    "ml_library": {
        "pytorch": ["torch", "py torch"],
        "tensorflow": ["tf", "tensor flow"],
        "numpy": ["num py"],                                                  
        "keras": [],
        "jax": [],
        "scikit-learn": ["sklearn", "scikit learn", "scikit"],
        "xgboost": [],
        "lightgbm": ["light gbm"],
        "catboost": [],
        "pandas": [],
        "numpy": [],
        "scipy": [],
        "polars": [],
        "statsmodels": [],
        "matplotlib": [],
        "seaborn": [],
        "plotly": ["plotly dash", "dash"],
        "opencv": ["open cv"],
        "spacy": [],
        "nltk": [],
        "hugging face": ["huggingface", "hugging face transformers", "hf transformers", "transformers library"],
        "pytorch lightning": ["lightning"],
        "onnx": ["onnx runtime"],
        "tensorrt": [],
        "prophet": ["facebook prophet"],
        "optuna": [],
        "shap": [],
        "pyspark ml": ["spark mllib", "mllib"],
        "dask": [],
        "ray": ["ray tune", "ray serve"],
        "numba": [],
        "jupyter": ["jupyter notebook", "jupyter notebooks", "jupyterlab", "ipython"],
    },

    # ------------------------------------------------------------------ LLM / GenAI
    "llm_genai": {
        "llm": ["llms", "llm apis", "large language models", "large language model", "foundation models", "foundation model"],
        "generative ai": ["genai", "gen ai", "generative artificial intelligence"],
        "rag": ["retrieval augmented generation", "retrieval-augmented generation", "retrieval augmented"],
        "prompt engineering": ["prompting", "prompt design"],
        "fine-tuning": ["fine tuning", "finetuning", "llm fine-tuning", "model fine-tuning"],
        "lora": ["qlora", "peft", "parameter efficient fine tuning"],
        "embeddings": ["text embeddings", "vector embeddings", "embedding models", "embedding pipeline"],
        "semantic search": ["vector search", "similarity search", "hybrid search"],
        "ai agents": ["agents", "agentic ai", "llm agents", "autonomous agents", "agentic workflows", "multi-agent systems", "multi agent systems"],
        "function calling": ["tool calling", "tool use"],
        "mcp": ["model context protocol"],
        "llm evaluation": ["llm evals", "evals", "model evals", "ai evaluation"],
        "guardrails": ["llm guardrails", "ai guardrails", "ai safety"],
        "llmops": ["llm ops"],
        "openai api": ["openai", "gpt", "gpt-4", "gpt 4", "gpt-4o", "chatgpt", "gpt-5"],
        "anthropic api": ["anthropic", "claude"],
        "gemini": ["google gemini", "palm"],
        "llama": ["llama 2", "llama 3", "meta llama"],
        "mistral": ["mixtral"],
        "langchain": ["lang chain"],
        "langgraph": ["lang graph"],
        "llamaindex": ["llama index", "gpt index"],
        "openai agents sdk": ["agents sdk"],
        "crewai": ["crew ai"],
        "autogen": [],
        "dspy": [],
        "semantic kernel": [],
        "haystack": [],
        "vllm": [],
        "ollama": [],
        "text generation inference": ["tgi"],
        "langfuse": [],
        "langsmith": [],
        "ragas": [],
        "pinecone": [],
        "weaviate": [],
        "qdrant": [],
        "milvus": ["zilliz"],
        "chroma": ["chromadb", "chroma db"],
        "pgvector": ["pg vector"],
        "faiss": [],
        "vector databases": ["vector database", "vector db", "vector store", "vector stores"],
        "knowledge graphs": ["knowledge graph"],
        "multimodal": ["multimodal models", "vision language models", "vlm", "vlms"],
        "ocr": ["optical character recognition", "document ai"],
        "tokenization": [],
        "quantization": [],
        "distillation": ["knowledge distillation", "model distillation"],
        "aws bedrock": ["bedrock", "amazon bedrock"],
        "azure openai": ["azure openai service"],
        "vertex ai": ["google vertex ai", "vertex"],
    },

    # ------------------------------------------------------------------ MLOps
    "mlops": {
        "mlops": ["ml ops", "machine learning operations", "ml operations", "ml pipelines", 'machine learning pipelines'],
        "mlflow": ["ml flow"],
        "kubeflow": [],
        "weights & biases": ["wandb", "w&b", "weights and biases"],
        "dvc": ["data version control"],
        "sagemaker": ["aws sagemaker", "amazon sagemaker"],
        "azure machine learning": ["azure ml", "azureml"],
        "databricks ml": ["databricks mlflow"],
        "feature store": ["feature stores", "feast", "tecton"],
        "model serving": ["model deployment", "model inference", "inference", "ml deployment"],
        "bentoml": [],
        "triton": ["triton inference server", "nvidia triton"],
        "seldon": ["seldon core"],
        "kserve": ["kfserving"],
        "model monitoring": ["ml monitoring", "drift detection", "data drift", "model drift"],
        "evidently": ["evidently ai"],
        "experiment tracking": [],
        "distributed training": ["multi-gpu training", "deepspeed", "fsdp", "horovod"],
        "gpu": ["gpus", "gpu computing", "nvidia gpus"],
    },

    # ------------------------------------------------------------------ data engineering
    "data_engineering": {
        "etl": ["elt", "etl/elt", "elt/etl", "etl pipelines", "data pipelines", "data pipeline"],
        "data modeling": ["data modelling", "dimensional modeling", "dimensional modelling", "star schema", "kimball"],
        "data warehousing": ["data warehouse", "dwh", "edw"],
        "data lake": ["data lakes", "lakehouse", "data lakehouse"],
        "apache spark": ["spark", "pyspark", "spark sql", "spark streaming"],
        "apache kafka": ["kafka", "confluent", "confluent kafka"],
        "apache flink": ["flink"],
        "apache beam": ["beam"],
        "apache airflow": ["airflow", "aws mwaa", "mwaa", "cloud composer"],
        "dagster": [],
        "prefect": [],
        "luigi": [],
        "dbt": ["dbt core", "dbt cloud", "data build tool"],
        "hadoop": ["hdfs", "mapreduce", "hive", "apache hive"],
        "apache iceberg": ["iceberg"],
        "delta lake": ["delta"],
        "apache hudi": ["hudi"],
        "parquet": ["apache parquet"],
        "avro": [],
        "fivetran": [],
        "airbyte": [],
        "stitch": [],
        "talend": [],
        "informatica": [],
        "ssis": ["sql server integration services"],
        "azure data factory": ["adf", "data factory"],
        "aws glue": ["glue"],
        "aws kinesis": ["kinesis"],
        "google pub/sub": ["pub/sub", "pubsub", "pub sub"],
        "dataflow": ["google dataflow", "cloud dataflow"],
        "streaming": ["stream processing", "real-time data", "real time data", "real-time processing"],
        "batch processing": [],
        "change data capture": ["cdc", "debezium"],
        "data quality": ["data validation", "great expectations", "soda"],
        "data governance": ["data lineage", "data catalog", "data catalogue", "unity catalog", "collibra", "alation"],
        "data observability": ["monte carlo"],
        "data mesh": [],
        "trino": ["presto", "starburst"],
        "duckdb": ["duck db"],
        "web scraping": ["scraping", "beautifulsoup", "scrapy", "selenium"],
        "apis": ["api", "rest api", "rest apis", "restful apis", "api integration"],
    },

    # ------------------------------------------------------------------ databases / warehouses
    "database": {
        "snowflake": [],
        "bigquery": ["google bigquery", "big query", "gbq"],
        "redshift": ["amazon redshift", "aws redshift"],
        "databricks": ["databricks lakehouse"],
        "azure synapse": ["synapse", "synapse analytics"],
        "microsoft fabric": ["fabric", "ms fabric"],
        "postgresql": ["postgres", "psql"],
        "mysql": [],
        "sql server": ["mssql", "ms sql", "microsoft sql server", "ms sql server"],
        "oracle": ["oracle database", "oracle db"],
        "sqlite": [],
        "mongodb": ["mongo", "mongo db"],
        "cassandra": ["apache cassandra"],
        "dynamodb": ["dynamo db", "aws dynamodb"],
        "redis": [],
        "elasticsearch": ["elastic search", "elastic", "opensearch", "elk"],
        "neo4j": ["graph databases", "graph database"],
        "clickhouse": [],
        "nosql": ["no sql"],
        "relational databases": ["rdbms", "relational database"],
        "cosmos db": ["azure cosmos db", "cosmosdb"],
    },

    # ------------------------------------------------------------------ cloud / infra / engineering
    "cloud_devops": {
        "aws": ["amazon web services", "aws cloud"],
        "azure": ["microsoft azure", "azure cloud"],
        "gcp": ["google cloud platform", "google cloud", "gcloud"],
        "aws lambda": ["lambda", "serverless"],
        "aws s3": ["s3", "amazon s3"],
        "aws ec2": ["ec2"],
        "docker": ["containers", "containerization", "containerisation"],
        "kubernetes": ["k8s", "eks", "aks", "gke", "helm"],
        "terraform": ["infrastructure as code", "iac", "cloudformation", "pulumi"],
        "ci/cd": ["ci cd", "cicd", "continuous integration", "continuous deployment", "continuous delivery", "ci/cd pipelines"],
        "github actions": [],
        "gitlab ci": ["gitlab"],
        "jenkins": [],
        "git": ["github", "version control", "bitbucket"],
        "linux": ["unix"],
        "devops": [],
        "microservices": ["microservice architecture"],
        "distributed systems": [],
        "system design": ["software architecture"],
        "fastapi": ["fast api"],
        "flask": [],
        "django": [],
        "streamlit": [],
        "gradio": [],
        "node.js": ["nodejs", "node"],
        "react": ["reactjs", "react.js"],
        "next.js": ["nextjs"],
        "graphql": [],
        "grpc": [],
        "celery": [],
        "rabbitmq": ["rabbit mq"],
        "prometheus": [],
        "grafana": [],
        "datadog": [],
        "opentelemetry": ["otel"],
        "testing": ["unit testing", "pytest", "test automation", "tdd"],
        "oop": ["object oriented programming", "object-oriented programming"],
        "data structures and algorithms": ["data structures", "algorithms", "dsa"],
        "security": ["cybersecurity", "information security"],
    },

    # ------------------------------------------------------------------ BI / analytics
    "bi_analytics": {
        "power bi": ["powerbi", "microsoft power bi", "power bi desktop", "power bi service"],
        "tableau": ["tableau desktop", "tableau server"],
        "looker": ["google looker"],
        "looker studio": ["google data studio", "data studio"],
        "qlik": ["qlikview", "qlik sense"],
        "metabase": [],
        "apache superset": ["superset"],
        "mode": ["mode analytics"],
        "hex": [],
        "sigma": ["sigma computing"],
        "thoughtspot": [],
        "excel": ["microsoft excel", "ms excel", "advanced excel", "spreadsheets", "pivot tables", "vlookup", "xlookup"],
        "google sheets": [],
        "power query": [],
        "ssrs": ["sql server reporting services"],
        "ssas": ["sql server analysis services"],
        "data visualization": ["data visualisation", "dashboards", "dashboarding", "dashboard development", "reporting", "data storytelling"],
        "data analysis": ["data analytics", "analytics", "exploratory data analysis", "eda"],
        "business intelligence": ["bi"],
        "kpis": ["kpi", "metrics", "kpi reporting", "metric design"],
        "semantic layer": ["metrics layer", "dbt semantic layer", "cube", "cube.dev"],
        "product analytics": ["product analysis"],
        "cohort analysis": ["retention analysis"],
        "funnel analysis": ["conversion analysis"],
        "customer lifetime value": ["ltv", "clv", "cltv"],
        "churn analysis": ["churn prediction", "churn modeling"],
        "marketing analytics": ["attribution", "marketing mix modeling", "mmm", "attribution modeling"],
        "financial analysis": ["financial modeling", "financial modelling"],
        "google analytics": ["ga4", "google analytics 4"],
        "mixpanel": [],
        "amplitude": [],
        "segment": ["twilio segment"],
        "heap": [],
        "posthog": [],
        "adobe analytics": [],
        "google tag manager": ["gtm"],
        "salesforce": ["sfdc"],
        "sap": [],
    },

    # ------------------------------------------------------------------ ways of working
    "practice": {
        "agile": ["scrum", "kanban", "agile methodologies"],
        "jira": [],
        "confluence": [],
        "stakeholder management": ["stakeholder communication"],
        "requirements gathering": ["business requirements", "requirements analysis"],
        "data privacy": ["gdpr", "data protection", "pii"],
        "responsible ai": ["ai ethics", "ethical ai", "ai governance", "fairness"],
    },
}
# fmt: on

# Things the model sometimes returns that aren't technical skills (compared after _clean).
STOPWORDS: set[str] = {
    "communication", "communication skills", "written communication", "verbal communication",
    "teamwork", "team player", "collaboration", "leadership", "mentoring", "ownership",
    "problem solving", "problem-solving", "critical thinking", "analytical skills",
    "analytical thinking", "attention to detail", "time management", "self motivated",
    "self-starter", "curiosity", "adaptability", "creativity", "presentation skills",
    "english", "german", "french", "spanish", "dutch", "arabic",
    "bachelor's degree", "bachelors degree", "master's degree", "masters degree", "phd",
    "degree", "computer science", "mathematics", "engineering", "physics", "economics",
    "programming", "coding", "software", "software development", "software engineering",
    "technology", "it", "data", "tools", "cloud", "cloud computing", "ai", "artificial intelligence",
    "microsoft office", "ms office", "office 365", "word", "powerpoint", "outlook",
}


def _clean(raw: str) -> str:
    """lowercase, drop "(...)", turn - _ / into spaces, single spaces, trim punctuation.

    Keeps "+", "#", "." and "&" so "c++", "c#", "node.js" and "w&b" survive.
    """
    text = raw.lower()
    text = re.sub(r"\([^)]*\)", " ", text)       # "Amazon Web Services (AWS)" -> "amazon web services"
    text = re.sub(r"[-_/]", " ", text)           # "scikit-learn", "ci/cd" -> "scikit learn", "ci cd"
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,;:'\"`*")              # "Python." -> "python" (but "node.js" keeps its dot)


def _build_indexes() -> tuple[dict[str, str], dict[str, str]]:
    aliases: dict[str, str] = {}
    category: dict[str, str] = {}
    for cat, skills in SKILLS.items():
        for canonical, alias_list in skills.items():
            if canonical in category:
                raise ValueError(f"skill {canonical!r} is listed in two categories")
            category[canonical] = cat
            for name in [canonical, *alias_list]:
                key = _clean(name)
                if aliases.get(key, canonical) != canonical:
                    raise ValueError(f"alias {name!r} maps to {aliases[key]!r} and {canonical!r}")
                aliases[key] = canonical
    return aliases, category


# cleaned text -> canonical name, canonical name -> category. Built once at import.
ALIASES, SKILL_CATEGORY = _build_indexes()
_STOP = {_clean(s) for s in STOPWORDS}


def normalize_skill(raw: str | None) -> str | None:
    """Canonical skill name, or None if it's empty, junk, a stopword or too long."""
    if raw is None or not raw.strip():
        return None
    cleaned = _clean(raw)
    if not cleaned or cleaned in _STOP:
        return None
    canonical = ALIASES.get(cleaned, cleaned)
    if len(canonical) > MAX_SKILL_LEN:
        return None
    return canonical


def skill_category(skill: str) -> str:
    """Category of a CANONICAL skill ("pytorch" -> "ml_library"); "other" if unknown."""
    return SKILL_CATEGORY.get(skill, "other")