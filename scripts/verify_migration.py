"""验证生产库迁移 0005 结果：列、索引、回填状态。"""

from sqlalchemy import create_engine, inspect, text

eng = create_engine("mysql+pymysql://root:1234@127.0.0.1:3306/travel_agent?charset=utf8mb4")
insp = inspect(eng)
cols = [c["name"] for c in insp.get_columns("plan_versions")]
print("plan_versions cols:", cols)
idx = [i["name"] for i in insp.get_indexes("plan_versions")]
print("indexes:", idx)
with eng.connect() as conn:
    cnt = conn.execute(
        text("SELECT COUNT(*) FROM plan_versions WHERE snapshot_hash IS NOT NULL")
    ).scalar()
    total = conn.execute(text("SELECT COUNT(*) FROM plan_versions")).scalar()
    print(f"snapshot_hash 回填: {cnt}/{total}")

    # messages.user_id 也应存在（0004）
    mcols = [c["name"] for c in insp.get_columns("messages")]
    print("messages has user_id:", "user_id" in mcols)
