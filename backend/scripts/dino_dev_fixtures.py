"""Dev only (local database): a dev user, fixture "own reels" and one analysis for the /dev/dino page.

    python scripts/dino_dev_fixtures.py <samples_dir>      # run from backend/ ; samples_dir holds the *_before.mp4 sample clips

The reel embeddings are REAL DINOv2 embeddings of the sample clips, stored under media ids "fixture_<name>". The Instagram
tables are NOT touched by the other branch's code here: this script creates minimal ig_account / ig_reel tables only if they do
not exist, with obviously fake round-number metrics, so the page can show the metrics path. Nothing here is real account data.
"""
import asyncio
import sys
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.prysm_analysis import PrysmAnalysis  # noqa: E402
from app.models.user import User  # noqa: E402
from app.studio.dino import store  # noqa: E402

EMAIL, USERNAME, PASSWORD = "dino-dev@example.com", "dino_dev", "DevDino#2026x"
FIXTURES = {"fixture_dance": ("dance_before.mp4", 100, 1.1), "fixture_saree": ("saree_before.mp4", 200, 0.9),
            "fixture_reel291": ("reel291_before.mp4", 300, 1.4), "fixture_rawspeech": ("rawspeech_before.mp4", 400, 0.7)}


async def main(samples: Path):
    engine = create_async_engine(settings.DATABASE_URL)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as db:
        user = (await db.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
        if user is None:
            user = User(email=EMAIL, username=USERNAME, hashed_pw=hash_password(PASSWORD), is_verified=True)
            db.add(user)
            await db.commit()
        await db.execute(text("create table if not exists ig_account (id uuid primary key, user_id uuid)"))
        await db.execute(text("create table if not exists ig_reel (id uuid primary key, account_id uuid, media_id varchar(60), permalink text, posted_at timestamptz, "
                              "duration_s float, like_count int, comments_count int, performance_index float, performance_basis varchar(20), watch_through float)"))
        acc = (await db.execute(text("select id from ig_account where user_id = :u"), {"u": user.id})).scalar()
        if acc is None:
            acc = uuid.uuid4()
            await db.execute(text("insert into ig_account values (:a, :u)"), {"a": acc, "u": user.id})
        for media_id, (clip, likes, perf) in FIXTURES.items():
            res = await asyncio.to_thread(store.embed_reel, str(samples / clip))
            await store.save_reel_embedding(db, user.id, media_id, res["mean"], res["frame_count"])
            await db.execute(text("delete from ig_reel where account_id = :a and media_id = :m"), {"a": acc, "m": media_id})
            await db.execute(text("insert into ig_reel (id, account_id, media_id, like_count, comments_count, performance_index, performance_basis) "
                                  "values (:i, :a, :m, :l, :c, :p, 'views')"), {"i": uuid.uuid4(), "a": acc, "m": media_id, "l": likes, "c": likes // 10, "p": perf})
        an = PrysmAnalysis(user_id=user.id, video_filename="fixture_analysis.mp4")
        db.add(an)
        await db.commit()
        print("user:", EMAIL, "| analysis id:", an.id)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
