from __future__ import annotations
import importlib.util
from pathlib import Path

SCRIPT=Path(__file__).resolve().parents[1]/"scripts/korea_tech_factor.py"
spec=importlib.util.spec_from_file_location("korea_factor",SCRIPT)
assert spec and spec.loader
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def bars(values,start=1):
    return [{"date":f"2026-01-{i+start:02d}","close":float(v)} for i,v in enumerate(values)]


def test_latest_before_excludes_same_day():
    m={"2026-01-01":1,"2026-01-02":2}
    assert mod.latest_before(m,"2026-01-02")==("2026-01-01",1)


def test_relative_map_aligns_by_date():
    asset=[{"date":"2026-01-01","close":10},{"date":"2026-01-03","close":12},{"date":"2026-01-04","close":15}]
    bench=[{"date":"2026-01-01","close":10},{"date":"2026-01-02","close":11},{"date":"2026-01-04","close":12}]
    # h=1 return maps intersect only on Jan 4: asset +25%, benchmark +9.09%.
    out=mod.relative_map(asset,bench,1)
    assert list(out)==["2026-01-04"]
    assert round(out["2026-01-04"],6)==round(.25-(12/11-1),6)


def test_score_for_day_point_in_time():
    maps={k:{"2026-01-01":.8,"2026-01-02":.2} for k in mod.WEIGHTS}
    score=mod.score_for_day("2026-01-02",maps)
    assert score and score["score"]==80
    assert set(score["source_cutoffs"].values())=={"2026-01-01"}


def test_backtest_strong_minus_weak_positive():
    rows=[]
    for i in range(5):
        rows.append({"date":f"2026-01-{i+1:02d}","code":"A","score":80,"regime":"strong","t1":.02,"t5":.04,"t20":.08})
        rows.append({"date":f"2026-02-{i+1:02d}","code":"A","score":20,"regime":"weak","t1":-.01,"t5":-.02,"t20":-.03})
    out=mod.backtest(rows)
    assert out["horizons"]["t1"]["strong_minus_weak"]==.03
    assert out["regimes"]["strong"]["t5"]["win_rate"]==1


def test_target_rows_horizons():
    factor=[{"date":f"2026-01-{i:02d}","score":50,"regime":"neutral"} for i in range(1,25)]
    series={}
    for code in mod.TARGETS:
        series[f"target:{code}"]=[{"date":f"2026-01-{i:02d}","close":float(i)} for i in range(1,25)]
    rows=mod.target_rows(series,factor)
    first=next(x for x in rows if x["code"]=="512480")
    assert first["date"]=="2026-01-01"
    assert first["t1"]==1.0
    assert first["t5"]==5.0
    assert first["t20"]==20.0


def test_freeze_forward_is_append_once(tmp_path):
    path=tmp_path/"forward.json"
    payload={"generated_at":"x","factor":{"decision_date":"2026-01-02","score":70,"regime":"strong","components":{},"source_cutoffs":{}}}
    a=mod.freeze_forward(path,payload)
    b=mod.freeze_forward(path,{**payload,"generated_at":"later"})
    assert len(a["snapshots"])==1
    assert len(b["snapshots"])==1
    assert b["snapshots"][0]["frozen_at"]=="x"
