"""数据模型：联赛 / 球队 / 球员 / 比赛 / 比赛详情 / 伤病 / 预测。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)  # e.g. eng.1 / nba
    name: Mapped[str] = mapped_column(String(128))
    sport: Mapped[str] = mapped_column(String(16), default="football")  # football | basketball
    season: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    teams: Mapped[list["Team"]] = relationship(back_populates="league")


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("league_code", "source_id", name="uq_team"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32))        # ESPN team id
    league_code: Mapped[str] = mapped_column(String(32))      # eng.1 / nba
    name: Mapped[str] = mapped_column(String(128))
    short_name: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    abbreviation: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # 近期统计，随比赛更新
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    draws: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(Integer, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)

    league_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leagues.id"), nullable=True)
    league: Mapped[Optional["League"]] = relationship(back_populates="teams")
    players: Mapped[list["Player"]] = relationship(back_populates="team")
    home_matches: Mapped[list["Match"]] = relationship(
        "Match", foreign_keys="Match.home_team_id", back_populates="home_team"
    )
    away_matches: Mapped[list["Match"]] = relationship(
        "Match", foreign_keys="Match.away_team_id", back_populates="away_team"
    )


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("league_code", "source_id", name="uq_player"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32))
    league_code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    position: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    jersey: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team: Mapped[Optional["Team"]] = relationship(back_populates="players")
    headshot_url: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # active | injured | ...


class Match(Base):
    """赛程与赛果。"""
    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("league_code", "source_id", name="uq_match"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32))
    league_code: Mapped[str] = mapped_column(String(32))
    sport: Mapped[str] = mapped_column(String(16), default="football")
    start_time: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled | in | final
    season: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    home_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    away_team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    home_team: Mapped[Optional["Team"]] = relationship(
        "Team", foreign_keys=[home_team_id], back_populates="home_matches"
    )
    away_team: Mapped[Optional["Team"]] = relationship(
        "Team", foreign_keys=[away_team_id], back_populates="away_matches"
    )

    home_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 足球专用
    home_half_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    away_half_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 篮球专用
    home_period_scores: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    away_period_scores: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON

    venue: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    detail_fetched: Mapped[bool] = mapped_column(default=False)

    details: Mapped[list["MatchDetail"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )
    predictions: Mapped[list["Prediction"]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class MatchDetail(Base):
    """比赛详细数据：事件、阵容、统计。"""
    __tablename__ = "match_details"
    __table_args__ = (UniqueConstraint("match_id", "kind", name="uq_detail"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    kind: Mapped[str] = mapped_column(String(32))   # events | lineups | stats | boxscore
    payload: Mapped[str] = mapped_column(Text)       # JSON 原始内容
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    match: Mapped["Match"] = relationship(back_populates="details")


class Injury(Base):
    """伤病数据。"""
    __tablename__ = "injuries"
    __table_args__ = (UniqueConstraint("league_code", "source_id", name="uq_injury"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[str] = mapped_column(String(32), default="")
    league_code: Mapped[str] = mapped_column(String(32))
    sport: Mapped[str] = mapped_column(String(16), default="football")
    player_id: Mapped[Optional[int]] = mapped_column(ForeignKey("players.id"), nullable=True)
    player_name: Mapped[str] = mapped_column(String(128))
    team_id: Mapped[Optional[int]] = mapped_column(ForeignKey("teams.id"), nullable=True)
    team_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    injury_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # out | day_to_day
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated: Mapped[Optional[date]] = mapped_column(Date, nullable=True)


class Prediction(Base):
    """模型预测结果。"""
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("matches.id"))
    model_name: Mapped[str] = mapped_column(String(64))
    sport: Mapped[str] = mapped_column(String(16), default="football")
    # 分类概率（胜/平/负 或 主胜/客胜）
    prob_home: Mapped[float] = mapped_column(Float, default=0.0)
    prob_draw: Mapped[float] = mapped_column(Float, default=0.0)
    prob_away: Mapped[float] = mapped_column(Float, default=0.0)
    predicted_label: Mapped[str] = mapped_column(String(16), default="")  # home | draw | away
    # 回归量（可选）
    predicted_home_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    predicted_away_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    match: Mapped["Match"] = relationship(back_populates="predictions")


def create_all() -> None:
    """创建所有表。"""
    from ball.db.engine import engine

    Base.metadata.create_all(engine)
