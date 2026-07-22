"""PostgreSQL 知识库模型 - KnowledgeBase、KnowledgeFile、评估相关表"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


class KnowledgeBase(Base):
    """知识库模型"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("kb_id", name="uq_knowledge_bases_kb_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    kb_type = Column(String(32), nullable=False, index=True)
    embedding_model_spec = Column(String(512))
    llm_model_spec = Column(String(512))
    query_params = Column(JSON_VALUE)
    additional_params = Column(JSON_VALUE)
    share_config = Column(JSON_VALUE)
    mindmap = Column(JSON_VALUE)
    mindmap_file_ids = Column(JSON_VALUE)
    mindmap_metadata = Column(JSON_VALUE)
    sample_questions = Column(JSON_VALUE)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeFile(Base):
    """知识文件模型"""

    __tablename__ = "knowledge_files"
    __table_args__ = (UniqueConstraint("file_id", name="uq_knowledge_files_file_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"), index=True)
    filename = Column(String(512), nullable=False)
    original_filename = Column(String(512))
    file_type = Column(String(64))
    path = Column(String(1024))
    minio_url = Column(String(1024))
    markdown_file = Column(String(1024))
    status = Column(String(32), default="uploaded", index=True)
    content_hash = Column(String(128), index=True)
    file_size = Column(BigInteger)
    chunk_count = Column(Integer, default=0)
    token_count = Column(BigInteger, default=0)
    content_type = Column(String(64))
    processing_params = Column(JSON_VALUE)
    is_folder = Column(Boolean, default=False)
    error_message = Column(Text)
    created_by = Column(String(64))
    updated_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeChunk(Base):
    """知识库 Chunk 模型"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_knowledge_chunks_chunk_id"),
        Index("ix_knowledge_chunks_file_id", "file_id"),
        Index("ix_knowledge_chunks_kb_id", "kb_id"),
        Index("ix_knowledge_chunks_graph_indexed", "graph_indexed"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    start_char_pos = Column(Integer)
    end_char_pos = Column(Integer)
    start_token_pos = Column(Integer)
    end_token_pos = Column(Integer)
    graph_indexed = Column(Boolean, default=False)
    ent_ids = Column(JSON_VALUE)
    tags = Column(JSON_VALUE)
    chunk_metadata = Column(JSON_VALUE)
    extraction_result = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class AcademicPaper(Base):
    """知识库内的学术论文领域记录。"""

    __tablename__ = "academic_papers"
    __table_args__ = (
        UniqueConstraint("kb_id", "file_id", name="uq_academic_papers_kb_file"),
        UniqueConstraint("kb_id", "paper_id", name="uq_academic_papers_kb_paper"),
        Index("ix_academic_papers_kb_year", "kb_id", "publication_year"),
        Index("ix_academic_papers_kb_doi", "kb_id", "doi"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    paper_id = Column(String(64), nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(Text, nullable=False)
    abstract = Column(Text)
    authors = Column(JSON_VALUE)
    publication_year = Column(Integer)
    venue = Column(String(512))
    doi = Column(String(512))
    keywords = Column(JSON_VALUE)
    language = Column(String(16))
    external_ids = Column(JSON_VALUE)
    citation_count = Column(Integer)
    metadata_source = Column(String(64), nullable=False, default="document")
    metadata_status = Column(String(32), nullable=False, default="extracted")
    metadata_error = Column(Text)
    metadata_revision = Column(Integer, nullable=False, default=1)
    indexed_revision = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class ResearchSearchRun(Base):
    """科研检索运行记录，用于追踪配置、阶段耗时和明确失败。"""

    __tablename__ = "research_search_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_research_search_runs_run_id"),
        Index("ix_research_search_runs_kb_created", "kb_id", "created_at"),
        Index("ix_research_search_runs_uid_created", "uid", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    uid = Column(String(64), nullable=False, index=True)
    raw_query = Column(Text, nullable=False)
    rewritten_query = Column(Text)
    rewrite_keywords = Column(JSON_VALUE)
    model_config_json = Column("model_config", JSON_VALUE)
    retrieval_config = Column(JSON_VALUE)
    status = Column(String(32), nullable=False, default="running", index=True)
    stage_timings = Column(JSON_VALUE)
    result_count = Column(Integer, nullable=False, default=0)
    error_type = Column(String(128))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    started_at = Column(DateTime(timezone=True), default=utc_now_naive)
    completed_at = Column(DateTime(timezone=True))


class AcademicPaperAnalysisRun(Base):
    """论文分析流水线运行记录，只保存结构化阶段结果，不保存模型原始响应。"""

    __tablename__ = "academic_paper_analysis_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_academic_paper_analysis_runs_id"),
        Index("ix_academic_paper_analysis_runs_paper_created", "academic_paper_id", "created_at"),
        Index("ix_academic_paper_analysis_runs_uid_created", "uid", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    academic_paper_id = Column(
        Integer, ForeignKey("academic_papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uid = Column(String(64), nullable=False, index=True)
    model_config_json = Column("model_config", JSON_VALUE)
    strategy = Column(String(32), nullable=False, default="multi_agent", index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    stage = Column(String(64))
    stage_results = Column(JSON_VALUE)
    result = Column(JSON_VALUE)
    error_type = Column(String(128))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class AcademicPaperAnalysisEvaluation(Base):
    """以人工盲审比较单 Agent 与多 Agent 论文分析的实验。"""

    __tablename__ = "academic_paper_analysis_evaluations"
    __table_args__ = (
        UniqueConstraint("evaluation_id", name="uq_academic_paper_analysis_evaluations_id"),
        Index("ix_academic_paper_analysis_evaluations_kb_created", "kb_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    evaluation_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    model_config_json = Column("model_config", JSON_VALUE, nullable=False)
    rubric_version = Column(String(32), nullable=False, default="analysis-blind-v1")
    status = Column(String(32), nullable=False, default="queued", index=True)
    paper_count = Column(Integer, nullable=False, default=0)
    completed_pairs = Column(Integer, nullable=False, default=0)
    task_id = Column(String(64))
    error_message = Column(Text)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class AcademicPaperAnalysisEvaluationItem(Base):
    """一个标注论文及其固定的 A/B 盲审报告顺序。"""

    __tablename__ = "academic_paper_analysis_evaluation_items"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_academic_paper_analysis_evaluation_items_id"),
        UniqueConstraint("evaluation_id", "academic_paper_id", name="uq_academic_paper_analysis_evaluation_paper"),
        Index("ix_academic_paper_analysis_evaluation_items_evaluation", "evaluation_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), nullable=False, unique=True, index=True)
    evaluation_id = Column(
        String(64),
        ForeignKey("academic_paper_analysis_evaluations.evaluation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    academic_paper_id = Column(
        Integer, ForeignKey("academic_papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_index = Column(Integer, nullable=False)
    single_run_id = Column(String(64))
    multi_run_id = Column(String(64))
    blind_assignment = Column(JSON_VALUE, nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    completed_at = Column(DateTime(timezone=True))


class AcademicPaperAnalysisEvaluationScore(Base):
    """评审者对同一论文 A/B 报告的盲审量表评分。"""

    __tablename__ = "academic_paper_analysis_evaluation_scores"
    __table_args__ = (
        UniqueConstraint("score_id", name="uq_academic_paper_analysis_evaluation_scores_id"),
        UniqueConstraint("item_id", "scorer_uid", name="uq_academic_paper_analysis_evaluation_scorer"),
        Index("ix_academic_paper_analysis_evaluation_scores_evaluation", "evaluation_id", "submitted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    score_id = Column(String(64), nullable=False, unique=True, index=True)
    evaluation_id = Column(
        String(64),
        ForeignKey("academic_paper_analysis_evaluations.evaluation_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id = Column(
        String(64),
        ForeignKey("academic_paper_analysis_evaluation_items.item_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scorer_uid = Column(String(64), nullable=False, index=True)
    blind_scores = Column(JSON_VALUE, nullable=False)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    submitted_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class ResearchUserStudy(Base):
    """科研罗盘真实用户评测研究，保留研究配置与匿名聚合边界。"""

    __tablename__ = "research_user_studies"
    __table_args__ = (
        UniqueConstraint("study_id", name="uq_research_user_studies_study_id"),
        Index("ix_research_user_studies_kb_created", "kb_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    study_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    consent_text = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="open", index=True)
    created_by = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    closed_at = Column(DateTime(timezone=True))


class ResearchUserStudyInvite(Base):
    """一次性匿名邀请码；只保存不可逆哈希，明文仅在创建时返回。"""

    __tablename__ = "research_user_study_invites"
    __table_args__ = (
        UniqueConstraint("invite_id", name="uq_research_user_study_invites_invite_id"),
        UniqueConstraint("token_hash", name="uq_research_user_study_invites_token_hash"),
        Index("ix_research_user_study_invites_study_status", "study_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    invite_id = Column(String(64), unique=True, nullable=False, index=True)
    study_id = Column(
        String(64),
        ForeignKey("research_user_studies.study_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(128), unique=True, nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    used_at = Column(DateTime(timezone=True))


class ResearchUserStudyResponse(Base):
    """匿名问卷响应；不保存姓名、账号、联系方式或设备指纹。"""

    __tablename__ = "research_user_study_responses"
    __table_args__ = (
        UniqueConstraint("response_id", name="uq_research_user_study_responses_response_id"),
        UniqueConstraint("invite_id", name="uq_research_user_study_responses_invite_id"),
        Index("ix_research_user_study_responses_study_submitted", "study_id", "submitted_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    response_id = Column(String(64), unique=True, nullable=False, index=True)
    study_id = Column(
        String(64),
        ForeignKey("research_user_studies.study_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invite_id = Column(
        String(64),
        ForeignKey("research_user_study_invites.invite_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    research_stage = Column(String(32), nullable=False)
    research_experience = Column(String(32), nullable=False)
    task_scores = Column(JSON_VALUE, nullable=False)
    sus_scores = Column(JSON_VALUE, nullable=False)
    overall_rating = Column(Integer, nullable=False)
    recommend_score = Column(Integer, nullable=False)
    feedback = Column(Text)
    submitted_at = Column(DateTime(timezone=True), default=utc_now_naive)


class AcademicGraphPaper(Base):
    """学术图谱论文节点，可对应论文库论文或外部引用论文。"""

    __tablename__ = "academic_graph_papers"
    __table_args__ = (
        UniqueConstraint("graph_paper_id", name="uq_academic_graph_papers_id"),
        UniqueConstraint("kb_id", "identity_key", name="uq_academic_graph_papers_identity"),
        UniqueConstraint("kb_id", "academic_paper_id", name="uq_academic_graph_papers_local"),
        Index("ix_academic_graph_papers_kb_year", "kb_id", "publication_year"),
        Index("ix_academic_graph_papers_kb_s2", "kb_id", "semantic_scholar_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_paper_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    academic_paper_id = Column(Integer, ForeignKey("academic_papers.id", ondelete="SET NULL"), index=True)
    identity_key = Column(String(512), nullable=False)
    semantic_scholar_id = Column(String(64))
    external_ids = Column(JSON_VALUE)
    title = Column(Text, nullable=False)
    abstract = Column(Text)
    publication_year = Column(Integer)
    venue = Column(String(512))
    citation_count = Column(Integer)
    reference_count = Column(Integer)
    influential_citation_count = Column(Integer)
    is_open_access = Column(Boolean, default=False)
    open_access_url = Column(String(2048))
    is_library_paper = Column(Boolean, nullable=False, default=False)
    source = Column(String(64), nullable=False, default="semantic_scholar")
    last_synced_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class AcademicAuthor(Base):
    __tablename__ = "academic_authors"
    __table_args__ = (
        UniqueConstraint("author_id", name="uq_academic_authors_id"),
        UniqueConstraint("kb_id", "identity_key", name="uq_academic_authors_identity"),
        Index("ix_academic_authors_kb_name", "kb_id", "normalized_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    author_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    identity_key = Column(String(512), nullable=False)
    semantic_scholar_id = Column(String(64))
    name = Column(String(512), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    external_ids = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class AcademicTopic(Base):
    __tablename__ = "academic_topics"
    __table_args__ = (
        UniqueConstraint("topic_id", name="uq_academic_topics_id"),
        UniqueConstraint("kb_id", "normalized_name", name="uq_academic_topics_identity"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    topic_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(512), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    category = Column(String(128))
    source = Column(String(64), nullable=False, default="semantic_scholar")
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class AcademicGraphPaperAuthor(Base):
    __tablename__ = "academic_graph_paper_authors"
    __table_args__ = (
        UniqueConstraint("graph_paper_id", "author_id", name="uq_academic_graph_paper_authors_pair"),
        Index("ix_academic_graph_paper_authors_author", "author_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_paper_id = Column(
        String(64), ForeignKey("academic_graph_papers.graph_paper_id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id = Column(String(64), ForeignKey("academic_authors.author_id", ondelete="CASCADE"), nullable=False)
    author_order = Column(Integer, nullable=False, default=0)


class AcademicGraphPaperTopic(Base):
    __tablename__ = "academic_graph_paper_topics"
    __table_args__ = (UniqueConstraint("graph_paper_id", "topic_id", name="uq_academic_graph_paper_topics_pair"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    graph_paper_id = Column(
        String(64), ForeignKey("academic_graph_papers.graph_paper_id", ondelete="CASCADE"), nullable=False, index=True
    )
    topic_id = Column(
        String(64), ForeignKey("academic_topics.topic_id", ondelete="CASCADE"), nullable=False, index=True
    )


class AcademicCitation(Base):
    __tablename__ = "academic_citations"
    __table_args__ = (
        UniqueConstraint("citation_id", name="uq_academic_citations_id"),
        UniqueConstraint("kb_id", "citing_paper_id", "cited_paper_id", name="uq_academic_citations_edge"),
        Index("ix_academic_citations_citing", "citing_paper_id"),
        Index("ix_academic_citations_cited", "cited_paper_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    citation_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    citing_paper_id = Column(
        String(64), ForeignKey("academic_graph_papers.graph_paper_id", ondelete="CASCADE"), nullable=False
    )
    cited_paper_id = Column(
        String(64), ForeignKey("academic_graph_papers.graph_paper_id", ondelete="CASCADE"), nullable=False
    )
    contexts = Column(JSON_VALUE)
    intents = Column(JSON_VALUE)
    is_influential = Column(Boolean, default=False)
    source = Column(String(64), nullable=False, default="semantic_scholar")
    last_synced_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class AcademicGraphSyncRun(Base):
    __tablename__ = "academic_graph_sync_runs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_academic_graph_sync_runs_id"),
        Index("ix_academic_graph_sync_runs_kb_created", "kb_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False, unique=True, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    uid = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending", index=True)
    requested_paper_ids = Column(JSON_VALUE)
    sync_config = Column(JSON_VALUE)
    processed_papers = Column(Integer, nullable=False, default=0)
    graph_papers = Column(Integer, nullable=False, default=0)
    citations = Column(Integer, nullable=False, default=0)
    authors = Column(Integer, nullable=False, default=0)
    topics = Column(Integer, nullable=False, default=0)
    conflict_count = Column(Integer, nullable=False, default=0)
    error_type = Column(String(128))
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class AcademicMetadataConflict(Base):
    __tablename__ = "academic_metadata_conflicts"
    __table_args__ = (
        UniqueConstraint("conflict_id", name="uq_academic_metadata_conflicts_id"),
        Index("ix_academic_metadata_conflicts_run", "run_id"),
        Index("ix_academic_metadata_conflicts_paper", "academic_paper_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conflict_id = Column(String(64), nullable=False, unique=True, index=True)
    run_id = Column(
        String(64), ForeignKey("academic_graph_sync_runs.run_id", ondelete="CASCADE"), nullable=False, index=True
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    academic_paper_id = Column(Integer, ForeignKey("academic_papers.id", ondelete="SET NULL"))
    conflict_type = Column(String(128), nullable=False)
    field_name = Column(String(128))
    local_value = Column(JSON_VALUE)
    remote_value = Column(JSON_VALUE)
    message = Column(Text, nullable=False)
    resolution_status = Column(String(32), nullable=False, default="unresolved")
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class KnowledgeGraphEntity(Base):
    """知识图谱实体"""

    __tablename__ = "knowledge_graph_entities"
    __table_args__ = (
        UniqueConstraint("entity_id", name="uq_knowledge_graph_entities_entity_id"),
        UniqueConstraint("kb_id", "normalized_name", "label", name="uq_knowledge_graph_entities_identity"),
        Index("ix_knowledge_graph_entities_kb_id", "kb_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    label = Column(String(128), nullable=False)
    name = Column(String(512), nullable=False)
    attributes = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphEntityMention(Base):
    """知识图谱实体在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_entity_mentions"
    __table_args__ = (
        UniqueConstraint("entity_id", "chunk_id", name="uq_knowledge_graph_entity_mentions_entity_chunk"),
        Index("ix_knowledge_graph_entity_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_entity_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_entity_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class KnowledgeGraphTriple(Base):
    """知识图谱三元组"""

    __tablename__ = "knowledge_graph_triples"
    __table_args__ = (
        UniqueConstraint("triple_id", name="uq_knowledge_graph_triples_triple_id"),
        Index("ix_knowledge_graph_triples_kb_id", "kb_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    source_entity_id = Column(
        String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id = Column(
        String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relation_type = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphTripleMention(Base):
    """知识图谱三元组在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_triple_mentions"
    __table_args__ = (
        UniqueConstraint("triple_id", "chunk_id", name="uq_knowledge_graph_triple_mentions_triple_chunk"),
        Index("ix_knowledge_graph_triple_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_triple_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_triple_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), ForeignKey("knowledge_graph_triples.triple_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    text = Column(Text)
    extractor_type = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class EvaluationDataset(Base):
    """评估数据集模型"""

    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("dataset_id", name="uq_evaluation_datasets_dataset_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    item_count = Column(Integer, default=0)
    has_gold_chunks = Column(Boolean, default=False)
    has_gold_answers = Column(Boolean, default=False)
    build_metadata = Column(JSON_VALUE)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class EvaluationDatasetItem(Base):
    """评估数据集题目模型"""

    __tablename__ = "evaluation_dataset_items"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_evaluation_dataset_items_item_id"),
        UniqueConstraint("dataset_id", "item_index", name="uq_evaluation_dataset_items_dataset_index"),
        Index("ix_evaluation_dataset_items_dataset_index", "dataset_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), unique=True, nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class EvaluationRun(Base):
    """评估运行模型"""

    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_evaluation_runs_run_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="SET NULL"),
        index=True,
    )
    experiment_id = Column(String(64), index=True)
    variant_id = Column(String(64), index=True)
    status = Column(String(32), default="running", index=True)
    retrieval_config = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    overall_score = Column(Float)
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), default=utc_now_naive, index=True)
    completed_at = Column(DateTime(timezone=True))
    created_by = Column(String(64))


class EvaluationExperiment(Base):
    """一组共享基准和模型条件的可复现实验。"""

    __tablename__ = "evaluation_experiments"
    __table_args__ = (UniqueConstraint("experiment_id", name="uq_evaluation_experiments_experiment_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    experiment_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    source_kb_id = Column(
        String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id = Column(
        String(64), ForeignKey("evaluation_datasets.dataset_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status = Column(String(32), nullable=False, default="queued", index=True)
    baseline_variant_id = Column(String(64))
    dataset_fingerprint = Column(String(128), nullable=False)
    corpus_snapshot = Column(JSON_VALUE)
    shared_config = Column(JSON_VALUE)
    comparison_report = Column(JSON_VALUE)
    total_variants = Column(Integer, nullable=False, default=0)
    completed_variants = Column(Integer, nullable=False, default=0)
    task_id = Column(String(64))
    error_message = Column(Text)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive, index=True)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class EvaluationExperimentVariant(Base):
    """实验中的一个受控变量与其对应评估运行。"""

    __tablename__ = "evaluation_experiment_variants"
    __table_args__ = (
        UniqueConstraint("variant_id", name="uq_evaluation_experiment_variants_variant_id"),
        UniqueConstraint("experiment_id", "variant_index", name="uq_evaluation_experiment_variant_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    variant_id = Column(String(64), unique=True, nullable=False, index=True)
    experiment_id = Column(
        String(64),
        ForeignKey("evaluation_experiments.experiment_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    variant_index = Column(Integer, nullable=False)
    name = Column(String(255), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    retrieval_config = Column(JSON_VALUE, nullable=False)
    execution_type = Column(String(32), nullable=False, default="research_compass")
    provenance = Column(JSON_VALUE)
    input_snapshot = Column(JSON_VALUE)
    run_id = Column(String(64), ForeignKey("evaluation_runs.run_id", ondelete="SET NULL"), unique=True)
    status = Column(String(32), nullable=False, default="queued", index=True)
    metrics = Column(JSON_VALUE)
    overall_score = Column(Float)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))


class EvaluationRunItem(Base):
    """评估逐题结果模型"""

    __tablename__ = "evaluation_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "item_index", name="uq_evaluation_run_items_run_index"),
        Index("ix_evaluation_run_items_run_index", "run_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(64),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_item_id = Column(
        String(64), ForeignKey("evaluation_dataset_items.item_id", ondelete="SET NULL"), index=True
    )
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_document_hashes = Column(JSON_VALUE)
    gold_answer = Column(Text)
    generated_answer = Column(Text)
    retrieved_chunks = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
