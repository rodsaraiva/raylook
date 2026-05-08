from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, Field


class VoteItem(BaseModel):
    name: Optional[str]
    phone: Optional[str]
    qty: int


class ClosedPackage(BaseModel):
    id: str
    poll_title: str
    qty: int
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None
    confirmed_at: Optional[str] = None
    votes: List[VoteItem]
    status: str = "closed"
    rejected: Optional[bool] = False
    model_config = ConfigDict(extra="allow")


class OpenPackage(BaseModel):
    poll_id: str
    poll_title: str
    qty: int
    opened_at: Optional[str] = None
    votes: List[VoteItem]


class PackagesModel(BaseModel):
    open: List[OpenPackage]
    closed_today: List[ClosedPackage]
    closed_week: List[ClosedPackage]
    confirmed_today: List[ClosedPackage] = Field(default_factory=list)
    model_config = ConfigDict(extra="allow")


class EnqueteMetricsModel(BaseModel):
    today: int
    yesterday: int
    diff_yesterday: float
    pct_yesterday: float
    avg_7_days: float
    diff_avg: float
    pct_avg: float
    model_config = ConfigDict(extra="allow")


class VotoMetricsModel(BaseModel):
    today: int
    yesterday: int
    diff_yesterday: float
    pct_yesterday: float
    avg_7_days: float
    diff_avg: float
    pct_avg: float
    removed_today: int
    removed_yesterday: int
    diff_removed: int
    pct_removed: float
    by_poll_today: Dict[str, Any]
    by_poll_week: Dict[str, Any]
    by_customer_today: Dict[str, Any]
    by_customer_week: Dict[str, Any]
    by_hour: Dict[int, int]
    packages: PackagesModel
    model_config = ConfigDict(extra="allow")


class DashboardModel(BaseModel):
    generated_at: str
    enquetes: EnqueteMetricsModel
    votos: VotoMetricsModel
    model_config = ConfigDict(extra="allow")

