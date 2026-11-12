"""Private operation data; deliberately separate from chat/delivery tables."""
from datetime import datetime
from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OperationBase(DeclarativeBase):
    pass


class Job(OperationBase):
    __tablename__ = "xy_operation_jobs"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_key", name="uk_op_owner_request"),
        Index("idx_op_queue", "status", "lease_until"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    keyword: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    stage: Mapped[str] = mapped_column(String(80), default="等待执行")
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Profile(OperationBase):
    __tablename__ = "xy_operation_profiles"
    owner_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ProductRevision(OperationBase):
    __tablename__ = 'xy_operation_product_revisions'
    __table_args__ = (Index('idx_op_revision_job', 'owner_id', 'job_id', 'id'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey('xy_operation_jobs.id', ondelete='CASCADE'))
    label: Mapped[str] = mapped_column(String(80))
    content_hash: Mapped[str] = mapped_column(String(64))
    context_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Workflow(OperationBase):
    __tablename__ = 'xy_operation_workflows'
    __table_args__ = (UniqueConstraint('owner_id','request_key',name='uk_op_flow_request'),
                      Index('idx_op_flow_queue','status','lease_until'))
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger,index=True)
    root_job_id: Mapped[str] = mapped_column(String(32),index=True)
    job_id: Mapped[str] = mapped_column(String(32),index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24),default='queued')
    data: Mapped[dict] = mapped_column(JSON,default=dict)
    lease_token: Mapped[str | None] = mapped_column(String(32),nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime,nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)


class BatchReview(OperationBase):
    __tablename__ = 'xy_operation_batch_reviews'
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger,index=True)
    batch_id: Mapped[str] = mapped_column(String(32),index=True)
    status: Mapped[str] = mapped_column(String(24),default='pending')
    data: Mapped[dict] = mapped_column(JSON,default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow)


class ScheduleAdjustment(OperationBase):
    __tablename__ = 'xy_operation_schedule_adjustments'
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger,index=True)
    status: Mapped[str] = mapped_column(String(24),default='pending')
    data: Mapped[dict] = mapped_column(JSON,default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow)


class RepairReview(OperationBase):
    __tablename__ = 'xy_operation_repair_reviews'
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger,index=True)
    status: Mapped[str] = mapped_column(String(24),default='pending')
    data: Mapped[dict] = mapped_column(JSON,default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow)


class BusinessTemplate(OperationBase):
    __tablename__ = 'xy_operation_business_templates'
    __table_args__ = (UniqueConstraint('owner_id','name',name='uk_op_template_name'),)
    id: Mapped[str] = mapped_column(String(32),primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger,index=True)
    name: Mapped[str] = mapped_column(String(80))
    settings: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime,default=datetime.utcnow,onupdate=datetime.utcnow)


class PublicationSchedule(OperationBase):
    __tablename__ = 'xy_operation_schedules'
    __table_args__ = (Index('idx_op_schedule_due','due_at','job_id'),)
    job_id: Mapped[str] = mapped_column(String(32),primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger,index=True)
    batch_id: Mapped[str] = mapped_column(String(32),index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime)


class AiConnection(OperationBase):
    __tablename__ = 'xy_operation_ai_connections'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String(80))
    config: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Event(OperationBase):
    __tablename__ = "xy_operation_events"
    __table_args__ = (Index("idx_op_event_job", "job_id", "id"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32))
    owner_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="success")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MetricSnapshot(OperationBase):
    __tablename__ = 'xy_operation_metric_snapshots'
    __table_args__ = (Index('idx_op_metric_subject', 'owner_id', 'account_id', 'item_id', 'created_at'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger)
    account_id: Mapped[str] = mapped_column(String(80))
    item_id: Mapped[str] = mapped_column(String(64), default='')
    days: Mapped[int] = mapped_column(BigInteger, default=7)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Optimization(OperationBase):
    __tablename__ = 'xy_operation_optimizations'
    __table_args__ = (Index('idx_op_optimization_item', 'owner_id', 'account_id', 'item_id', 'status'),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    account_id: Mapped[str] = mapped_column(String(80))
    item_id: Mapped[str] = mapped_column(String(64), default='')
    status: Mapped[str] = mapped_column(String(24), default='pending')
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
