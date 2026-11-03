"""
READ-ONLY production database audit for Prysm.
Connects via DATABASE_PUBLIC_URL, gathers schema + data quality,
compares against SQLAlchemy model definitions, writes markdown report.
"""

import psycopg2
import psycopg2.extras
import re
import os
from datetime import datetime

DB_URL = os.environ["DATABASE_URL"]
OUT = os.path.join(os.path.dirname(__file__), "database_schema_audit.md")

# ── SQLAlchemy model definitions (hand-extracted) ──────────────────────────────
# Format: { table_name: { col_name: {type, nullable, default} } }
MODEL_SCHEMA = {
    "users": {
        "id":                       {"type": "UUID",     "nullable": False},
        "email":                    {"type": "VARCHAR",  "nullable": False},
        "username":                 {"type": "VARCHAR",  "nullable": False},
        "hashed_pw":                {"type": "VARCHAR",  "nullable": False},
        "full_name":                {"type": "VARCHAR",  "nullable": True},
        "avatar_url":               {"type": "VARCHAR",  "nullable": True},
        "is_active":                {"type": "BOOLEAN",  "nullable": False},
        "is_verified":              {"type": "BOOLEAN",  "nullable": False},
        "credits":                  {"type": "FLOAT",    "nullable": False},
        "plan":                     {"type": "ENUM",     "nullable": False},
        "razorpay_customer_id":     {"type": "VARCHAR",  "nullable": True},
        "razorpay_subscription_id": {"type": "VARCHAR",  "nullable": True},
        "billing_cycle":            {"type": "VARCHAR",  "nullable": True},
        "plan_started_at":          {"type": "DATETIME", "nullable": True},
        "plan_expires_at":          {"type": "DATETIME", "nullable": True},
        "stripe_customer_id":       {"type": "VARCHAR",  "nullable": True},
        "stripe_subscription_id":   {"type": "VARCHAR",  "nullable": True},
        "rollover_credits":         {"type": "FLOAT",    "nullable": False},
        "auth_provider":            {"type": "VARCHAR",  "nullable": False},
        "instagram_access_token":   {"type": "TEXT",     "nullable": True},
        "instagram_user_id":        {"type": "VARCHAR",  "nullable": True},
        "instagram_username":       {"type": "VARCHAR",  "nullable": True},
        "created_at":               {"type": "DATETIME", "nullable": False},
        "updated_at":               {"type": "DATETIME", "nullable": True},
    },
    "media": {
        "id":               {"type": "UUID",      "nullable": False},
        "user_id":          {"type": "UUID",      "nullable": False},
        "title":            {"type": "VARCHAR",   "nullable": True},
        "original_url":     {"type": "TEXT",      "nullable": True},
        "storage_path":     {"type": "TEXT",      "nullable": True},
        "storage_url":      {"type": "TEXT",      "nullable": True},
        "thumbnail_url":    {"type": "TEXT",      "nullable": True},
        "source":           {"type": "ENUM",      "nullable": False},
        "status":           {"type": "ENUM",      "nullable": False},
        "duration_sec":     {"type": "FLOAT",     "nullable": True},
        "file_size_bytes":  {"type": "BIGINT",    "nullable": True},
        "mime_type":        {"type": "VARCHAR",   "nullable": True},
        "width":            {"type": "INTEGER",   "nullable": True},
        "height":           {"type": "INTEGER",   "nullable": True},
        "fps":              {"type": "FLOAT",     "nullable": True},
        "transcript":       {"type": "TEXT",      "nullable": True},
        "audio_path":       {"type": "TEXT",      "nullable": True},
        "celery_task_id":   {"type": "VARCHAR",   "nullable": True},
        "created_at":       {"type": "DATETIME",  "nullable": False},
        "updated_at":       {"type": "DATETIME",  "nullable": True},
    },
    "analyses": {
        "id":                    {"type": "UUID",    "nullable": False},
        "user_id":               {"type": "UUID",    "nullable": False},
        "media_id":              {"type": "UUID",    "nullable": False},
        "status":                {"type": "ENUM",    "nullable": False},
        "error_message":         {"type": "TEXT",    "nullable": True},
        "hook_score":            {"type": "FLOAT",   "nullable": True},
        "hook_transcript":       {"type": "TEXT",    "nullable": True},
        "hook_retention_potential": {"type": "VARCHAR", "nullable": True},
        "hook_suggestions":      {"type": "JSON",    "nullable": True},
        "virality_score":        {"type": "INTEGER", "nullable": True},
        "virality_confidence":   {"type": "ENUM",    "nullable": True},
        "virality_reasons":      {"type": "JSON",    "nullable": True},
        "story_structure":       {"type": "JSON",    "nullable": True},
        "value_density":         {"type": "FLOAT",   "nullable": True},
        "emotional_hooks":       {"type": "JSON",    "nullable": True},
        "call_to_action":        {"type": "TEXT",    "nullable": True},
        "trend_alignment":       {"type": "VARCHAR", "nullable": True},
        "relevant_trends":       {"type": "JSON",    "nullable": True},
        "missing_trends":        {"type": "JSON",    "nullable": True},
        "niche_alignment":       {"type": "VARCHAR", "nullable": True},
        "captions":              {"type": "JSON",    "nullable": True},
        "hashtags":              {"type": "JSON",    "nullable": True},
        "titles":                {"type": "JSON",    "nullable": True},
        "pacing_score":          {"type": "FLOAT",   "nullable": True},
        "improvements":          {"type": "JSON",    "nullable": True},
        "thumbnail_suggestions": {"type": "JSON",    "nullable": True},
        "benchmark":             {"type": "JSON",    "nullable": True},
        "scenes":                {"type": "JSON",    "nullable": True},
        "segments":              {"type": "JSON",    "nullable": True},
        "credits_used":          {"type": "INTEGER", "nullable": True},
        "processing_time_sec":   {"type": "FLOAT",   "nullable": True},
        "created_at":            {"type": "DATETIME","nullable": False},
        "updated_at":            {"type": "DATETIME","nullable": True},
    },
    "prysm_analyses": {
        "id":                  {"type": "UUID",    "nullable": False},
        "user_id":             {"type": "UUID",    "nullable": True},
        "source_type":         {"type": "VARCHAR", "nullable": True},
        "source_url":          {"type": "TEXT",    "nullable": True},
        "video_filename":      {"type": "VARCHAR", "nullable": True},
        "niche":               {"type": "VARCHAR", "nullable": True},
        "platform":            {"type": "VARCHAR", "nullable": True},
        "brief":               {"type": "TEXT",    "nullable": True},
        "content_goal":        {"type": "VARCHAR", "nullable": True},
        "intent":              {"type": "VARCHAR", "nullable": True},
        "post_caption":        {"type": "TEXT",    "nullable": True},
        "post_hashtags":       {"type": "JSON",    "nullable": True},
        "post_likes":          {"type": "INTEGER", "nullable": True},
        "post_comments":       {"type": "INTEGER", "nullable": True},
        "post_views":          {"type": "INTEGER", "nullable": True},
        "post_uploader":       {"type": "VARCHAR", "nullable": True},
        "overall_score":       {"type": "INTEGER", "nullable": True},
        "score_label":         {"type": "VARCHAR", "nullable": True},
        "summary":             {"type": "TEXT",    "nullable": True},
        "top_priorities":      {"type": "JSON",    "nullable": True},
        "parameters":          {"type": "JSON",    "nullable": True},
        "frames":              {"type": "JSON",    "nullable": True},
        "first_frame_b64":     {"type": "TEXT",    "nullable": True},
        "raw_result":          {"type": "JSON",    "nullable": True},
        "cost_breakdown":      {"type": "JSON",    "nullable": True},
        "total_cost_usd":      {"type": "FLOAT",   "nullable": True},
        "processing_time_sec": {"type": "FLOAT",   "nullable": True},
        "transcript":          {"type": "TEXT",    "nullable": True},
        "audio_analysis":      {"type": "JSON",    "nullable": True},
        "edited_video_key":    {"type": "VARCHAR", "nullable": True},
        "edit_plan":           {"type": "JSON",    "nullable": True},
        "share_id":            {"type": "VARCHAR", "nullable": True},
        "is_public":           {"type": "BOOLEAN", "nullable": False},
        "created_at":          {"type": "DATETIME","nullable": True},
        "updated_at":          {"type": "DATETIME","nullable": True},
    },
    "chat_sessions": {
        "id":           {"type": "UUID",    "nullable": False},
        "user_id":      {"type": "UUID",    "nullable": False},
        "title":        {"type": "VARCHAR", "nullable": True},
        "is_active":    {"type": "BOOLEAN", "nullable": True},
        "context":      {"type": "JSON",    "nullable": True},
        "session_type": {"type": "VARCHAR", "nullable": False},
        "analysis_id":  {"type": "UUID",    "nullable": True},
        "created_at":   {"type": "DATETIME","nullable": False},
        "updated_at":   {"type": "DATETIME","nullable": True},
    },
    "chat_messages": {
        "id":          {"type": "UUID",    "nullable": False},
        "session_id":  {"type": "UUID",    "nullable": False},
        "role":        {"type": "VARCHAR", "nullable": False},
        "content":     {"type": "TEXT",    "nullable": False},
        "tokens_used": {"type": "INTEGER", "nullable": True},
        "cost_usd":    {"type": "FLOAT",   "nullable": True},
        "extra_data":  {"type": "JSON",    "nullable": True},
        "created_at":  {"type": "DATETIME","nullable": False},
    },
    "creator_profiles": {
        "id":                      {"type": "UUID",    "nullable": False},
        "user_id":                 {"type": "UUID",    "nullable": False},
        "total_videos_analyzed":   {"type": "INTEGER", "nullable": True},
        "avg_virality_score":      {"type": "FLOAT",   "nullable": True},
        "avg_hook_score":          {"type": "FLOAT",   "nullable": True},
        "avg_value_density":       {"type": "FLOAT",   "nullable": True},
        "avg_pacing_score":        {"type": "FLOAT",   "nullable": True},
        "primary_niche":           {"type": "VARCHAR", "nullable": True},
        "secondary_niches":        {"type": "JSON",    "nullable": True},
        "top_performing_topics":   {"type": "JSON",    "nullable": True},
        "best_performing_hooks":   {"type": "JSON",    "nullable": True},
        "recurring_ctas":          {"type": "JSON",    "nullable": True},
        "virality_trend":          {"type": "JSON",    "nullable": True},
        "hook_score_trend":        {"type": "JSON",    "nullable": True},
        "platform_performance":    {"type": "JSON",    "nullable": True},
        "embedding_id":            {"type": "VARCHAR", "nullable": True},
        "content_style_summary":   {"type": "TEXT",    "nullable": True},
        "recommendations":         {"type": "JSON",    "nullable": True},
        "created_at":              {"type": "DATETIME","nullable": False},
        "updated_at":              {"type": "DATETIME","nullable": True},
    },
    "viral_patterns": {
        "id":                 {"type": "UUID",    "nullable": False},
        "analysis_id":        {"type": "UUID",    "nullable": True},
        "hook_type":          {"type": "ENUM",    "nullable": True},
        "hook_duration_sec":  {"type": "FLOAT",   "nullable": True},
        "video_duration_sec": {"type": "FLOAT",   "nullable": True},
        "emotional_tone":     {"type": "ENUM",    "nullable": True},
        "topic_category":     {"type": "VARCHAR", "nullable": True},
        "scene_change_rate":  {"type": "FLOAT",   "nullable": True},
        "speech_pace_wpm":    {"type": "INTEGER", "nullable": True},
        "has_captions":       {"type": "BOOLEAN", "nullable": True},
        "has_music":          {"type": "BOOLEAN", "nullable": True},
        "has_face":           {"type": "BOOLEAN", "nullable": True},
        "face_emotion":       {"type": "VARCHAR", "nullable": True},
        "platform":           {"type": "VARCHAR", "nullable": True},
        "virality_score":     {"type": "INTEGER", "nullable": True},
        "engagement_ratio":   {"type": "FLOAT",   "nullable": True},
        "share_rate":         {"type": "FLOAT",   "nullable": True},
        "completion_rate":    {"type": "FLOAT",   "nullable": True},
        "feature_vector":     {"type": "JSON",    "nullable": True},
        "source":             {"type": "VARCHAR", "nullable": True},
        "created_at":         {"type": "DATETIME","nullable": False},
    },
    "creator_intelligence": {
        "id":                     {"type": "UUID",     "nullable": False},
        "user_id":                {"type": "UUID",     "nullable": False},
        "handle":                 {"type": "VARCHAR",  "nullable": False},
        "platform":               {"type": "VARCHAR",  "nullable": False},
        "status":                 {"type": "ENUM",     "nullable": True},
        "error_message":          {"type": "TEXT",     "nullable": True},
        "posts_scraped":          {"type": "INTEGER",  "nullable": True},
        "raw_posts":              {"type": "JSON",     "nullable": True},
        "best_posting_times":     {"type": "JSON",     "nullable": True},
        "best_video_length_sec":  {"type": "FLOAT",    "nullable": True},
        "top_hooks":              {"type": "JSON",     "nullable": True},
        "topic_clusters":         {"type": "JSON",     "nullable": True},
        "engagement_patterns":    {"type": "JSON",     "nullable": True},
        "avg_virality_score":     {"type": "FLOAT",    "nullable": True},
        "avg_engagement_rate":    {"type": "FLOAT",    "nullable": True},
        "follower_count":         {"type": "BIGINT",   "nullable": True},
        "posting_frequency_days": {"type": "FLOAT",    "nullable": True},
        "niche_summary":          {"type": "TEXT",     "nullable": True},
        "strategic_insights":     {"type": "JSON",     "nullable": True},
        "celery_task_id":         {"type": "VARCHAR",  "nullable": True},
        "created_at":             {"type": "DATETIME", "nullable": False},
        "updated_at":             {"type": "DATETIME", "nullable": True},
    },
    "competitor_analyses": {
        "id":                        {"type": "UUID",    "nullable": False},
        "user_id":                   {"type": "UUID",    "nullable": False},
        "competitor_handle":         {"type": "VARCHAR", "nullable": False},
        "platform":                  {"type": "VARCHAR", "nullable": False},
        "status":                    {"type": "ENUM",    "nullable": True},
        "error_message":             {"type": "TEXT",    "nullable": True},
        "top_performing_content":    {"type": "JSON",    "nullable": True},
        "viral_patterns":            {"type": "JSON",    "nullable": True},
        "engagement_strategies":     {"type": "JSON",    "nullable": True},
        "posting_schedule":          {"type": "JSON",    "nullable": True},
        "content_pillars":           {"type": "JSON",    "nullable": True},
        "gap_opportunities":         {"type": "JSON",    "nullable": True},
        "threat_level":              {"type": "VARCHAR", "nullable": True},
        "strategic_recommendations": {"type": "JSON",    "nullable": True},
        "celery_task_id":            {"type": "VARCHAR", "nullable": True},
        "created_at":                {"type": "DATETIME","nullable": False},
        "updated_at":                {"type": "DATETIME","nullable": True},
    },
    "clip_jobs": {
        "id":              {"type": "UUID",    "nullable": False},
        "user_id":         {"type": "UUID",    "nullable": False},
        "media_id":        {"type": "UUID",    "nullable": False},
        "status":          {"type": "ENUM",    "nullable": True},
        "error_message":   {"type": "TEXT",    "nullable": True},
        "max_clips":       {"type": "INTEGER", "nullable": True},
        "min_clip_sec":    {"type": "FLOAT",   "nullable": True},
        "max_clip_sec":    {"type": "FLOAT",   "nullable": True},
        "add_captions":    {"type": "BOOLEAN", "nullable": True},
        "caption_style":   {"type": "VARCHAR", "nullable": True},
        "clips":           {"type": "JSON",    "nullable": True},
        "total_clips":     {"type": "INTEGER", "nullable": True},
        "celery_task_id":  {"type": "VARCHAR", "nullable": True},
        "created_at":      {"type": "DATETIME","nullable": False},
        "updated_at":      {"type": "DATETIME","nullable": True},
    },
    "trending_audio": {
        "id":           {"type": "UUID",    "nullable": False},
        "niche":        {"type": "VARCHAR", "nullable": False},
        "platform":     {"type": "VARCHAR", "nullable": False},
        "audio_title":  {"type": "VARCHAR", "nullable": False},
        "artist_name":  {"type": "VARCHAR", "nullable": True},
        "usage_count":  {"type": "INTEGER", "nullable": True},
        "avg_likes":    {"type": "FLOAT",   "nullable": True},
        "genre":        {"type": "VARCHAR", "nullable": True},
        "mood":         {"type": "VARCHAR", "nullable": True},
        "co_hashtags":  {"type": "JSON",    "nullable": True},
        "week_start":   {"type": "DATETIME","nullable": False},
        "sample_posts": {"type": "JSON",    "nullable": True},
        "created_at":   {"type": "DATETIME","nullable": False},
    },
    "niche_hashtags": {
        "id":         {"type": "UUID",    "nullable": False},
        "niche":      {"type": "VARCHAR", "nullable": False},
        "platform":   {"type": "VARCHAR", "nullable": False},
        "hashtag":    {"type": "VARCHAR", "nullable": False},
        "frequency":  {"type": "INTEGER", "nullable": True},
        "avg_likes":  {"type": "FLOAT",   "nullable": True},
        "week_start": {"type": "DATETIME","nullable": False},
        "created_at": {"type": "DATETIME","nullable": False},
    },
    "niche_formats": {
        "id":              {"type": "UUID",    "nullable": False},
        "niche":           {"type": "VARCHAR", "nullable": False},
        "platform":        {"type": "VARCHAR", "nullable": False},
        "format_label":    {"type": "VARCHAR", "nullable": False},
        "format_type":     {"type": "VARCHAR", "nullable": True},
        "frequency":       {"type": "INTEGER", "nullable": True},
        "avg_likes":       {"type": "FLOAT",   "nullable": True},
        "avg_comments":    {"type": "FLOAT",   "nullable": True},
        "example_hooks":   {"type": "JSON",    "nullable": True},
        "source_creators": {"type": "JSON",    "nullable": True},
        "week_start":      {"type": "DATETIME","nullable": False},
        "created_at":      {"type": "DATETIME","nullable": False},
    },
    "timeline_feedback": {
        "id":          {"type": "UUID",    "nullable": False},
        "analysis_id": {"type": "UUID",    "nullable": False},
        "user_id":     {"type": "UUID",    "nullable": False},
        "events":      {"type": "JSON",    "nullable": False},
        "summary":     {"type": "TEXT",    "nullable": True},
        "created_at":  {"type": "DATETIME","nullable": False},
    },
    "performance_feedback": {
        "id":                      {"type": "UUID",    "nullable": False},
        "analysis_id":             {"type": "UUID",    "nullable": False},
        "user_id":                 {"type": "UUID",    "nullable": True},
        "actual_views":            {"type": "INTEGER", "nullable": True},
        "actual_likes":            {"type": "INTEGER", "nullable": True},
        "actual_comments":         {"type": "INTEGER", "nullable": True},
        "actual_saves":            {"type": "INTEGER", "nullable": True},
        "actual_shares":           {"type": "INTEGER", "nullable": True},
        "actual_reach":            {"type": "INTEGER", "nullable": True},
        "hours_after_posting":     {"type": "INTEGER", "nullable": True},
        "implemented_suggestions": {"type": "BOOLEAN", "nullable": True},
        "notes":                   {"type": "TEXT",    "nullable": True},
        "reported_at":             {"type": "DATETIME","nullable": True},
    },
}

SENSITIVE_COLS = {
    "hashed_pw", "password", "token", "secret", "api_key",
    "instagram_access_token", "email",
}

def redact(col_name, value):
    if value is None:
        return "NULL"
    col_lower = col_name.lower()
    if "hashed_pw" in col_lower or "password" in col_lower:
        return "[REDACTED]"
    if "token" in col_lower or "secret" in col_lower or "api_key" in col_lower:
        return "[REDACTED]"
    if col_lower == "email" and value:
        parts = str(value).split("@")
        return f"***@{parts[1]}" if len(parts) == 2 else "[REDACTED]"
    if "instagram_access_token" in col_lower and value:
        return "[REDACTED]"
    # Truncate very long text
    s = str(value)
    if len(s) > 120:
        return s[:120] + "…"
    return s

def fmt_pct(n, total):
    if total == 0:
        return "N/A"
    return f"{100*n/total:.1f}%"

def pg_type_category(pg_type):
    t = pg_type.lower()
    if any(x in t for x in ("character", "text", "varchar", "char")):
        return "text"
    return "other"

def connect():
    return psycopg2.connect(DB_URL, connect_timeout=15)

def get_tables(cur):
    cur.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'public'
        ORDER BY tablename
    """)
    return [r[0] for r in cur.fetchall()]

def get_columns(cur, table):
    cur.execute("""
        SELECT
            c.column_name,
            c.data_type,
            c.udt_name,
            c.character_maximum_length,
            c.is_nullable,
            c.column_default
        FROM information_schema.columns c
        WHERE c.table_schema = 'public' AND c.table_name = %s
        ORDER BY c.ordinal_position
    """, (table,))
    return cur.fetchall()

def get_pks(cur, table):
    cur.execute("""
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = %s
        ORDER BY kcu.ordinal_position
    """, (table,))
    return [r[0] for r in cur.fetchall()]

def get_fks(cur, table):
    cur.execute("""
        SELECT
            kcu.column_name,
            ccu.table_name AS foreign_table,
            ccu.column_name AS foreign_column,
            rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        JOIN information_schema.referential_constraints rc
          ON tc.constraint_name = rc.constraint_name AND tc.table_schema = rc.constraint_schema
        JOIN information_schema.constraint_column_usage ccu
          ON rc.unique_constraint_name = ccu.constraint_name AND rc.unique_constraint_schema = ccu.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = %s
    """, (table,))
    return cur.fetchall()

def get_indexes(cur, table):
    cur.execute("""
        SELECT
            i.relname AS index_name,
            ix.indisunique AS is_unique,
            array_agg(a.attname ORDER BY k.ord) AS columns
        FROM pg_index ix
        JOIN pg_class i ON i.oid = ix.indexrelid
        JOIN pg_class t ON t.oid = ix.indrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN LATERAL unnest(ix.indkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE n.nspname = 'public' AND t.relname = %s
        GROUP BY i.relname, ix.indisunique
        ORDER BY i.relname
    """, (table,))
    return cur.fetchall()

def get_row_count(cur, table):
    cur.execute(f'SELECT COUNT(*) FROM "{table}"')
    return cur.fetchone()[0]

def get_null_stats(cur, table, columns):
    total = get_row_count(cur, table)
    if total == 0:
        return total, {}
    stats = {}
    for col_name, data_type, udt_name, char_max_len, is_nullable, col_default in columns:
        # Null %
        cur.execute(f'SELECT COUNT(*) FROM "{table}" WHERE "{col_name}" IS NULL')
        null_count = cur.fetchone()[0]
        # Empty string % (text-ish columns only)
        empty_count = 0
        if pg_type_category(data_type) == "text":
            cur.execute(f"SELECT COUNT(*) FROM \"{table}\" WHERE \"{col_name}\" = ''")
            empty_count = cur.fetchone()[0]
        stats[col_name] = {"null_count": null_count, "empty_count": empty_count}
    return total, stats

def get_sample_rows(cur, table, columns):
    col_names = [c[0] for c in columns]
    cur.execute(f'SELECT * FROM "{table}" ORDER BY random() LIMIT 3')
    rows = cur.fetchall()
    return col_names, rows

def get_alembic_head(cur):
    try:
        cur.execute("SELECT version_num FROM alembic_version")
        rows = cur.fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []

def type_compatible(pg_type, udt_name, model_type):
    """Rough check: does pg_type roughly match model_type?"""
    p = pg_type.lower()
    u = udt_name.lower()
    m = model_type.upper()
    if m == "UUID":
        return "uuid" in u
    if m in ("VARCHAR", "TEXT", "STRING"):
        return any(x in p for x in ("character", "text", "uuid"))
    if m == "BOOLEAN":
        return "bool" in p
    if m in ("INTEGER", "INT"):
        return "int" in p and "big" not in p
    if m == "BIGINT":
        return "bigint" in p or "big" in p
    if m in ("FLOAT", "DOUBLE"):
        return any(x in p for x in ("double", "float", "real", "numeric"))
    if m in ("JSON", "JSONB"):
        return "json" in p
    if m == "DATETIME":
        return "timestamp" in p
    if m == "ENUM":
        return u.startswith("plan") or u.startswith("media") or u.startswith("analysis") \
               or u.startswith("scraping") or u.startswith("clip") or u.startswith("hook") \
               or u.startswith("emotional") or u.startswith("virality") or p == "user-defined"
    return True  # unknown, don't flag

# ── Main ───────────────────────────────────────────────────────────────────────

print("Connecting to Railway production database…")
conn = connect()
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
print("Connected.")

tables = get_tables(cur)
alembic_heads = get_alembic_head(cur)
print(f"Tables found: {tables}")
print(f"Alembic version(s): {alembic_heads}")

model_tables = set(MODEL_SCHEMA.keys())
live_tables  = set(tables)
# alembic_version is a meta table, not an app table
live_tables.discard("alembic_version")

table_data = {}
for table in tables:
    if table == "alembic_version":
        continue
    print(f"  auditing {table}…")
    cols    = get_columns(cur, table)
    pks     = get_pks(cur, table)
    fks     = get_fks(cur, table)
    idxs    = get_indexes(cur, table)
    total, null_stats = get_null_stats(cur, table, cols)
    col_names, sample_rows = get_sample_rows(cur, table, cols)
    table_data[table] = {
        "cols": cols, "pks": pks, "fks": fks, "idxs": idxs,
        "total": total, "null_stats": null_stats,
        "col_names": col_names, "sample_rows": sample_rows,
    }

cur.close()
conn.close()
print("Queries complete. Writing report…")

# ── Build markdown ─────────────────────────────────────────────────────────────

EXPECTED_HEAD = "j0k1l2m3n4o5"

lines = []
def w(s=""):
    lines.append(s)

w(f"# Prysm Production Database Audit")
w(f"")
w(f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
w(f"**Host:** `YOUR-DB-HOST:PORT` (Railway)")
w(f"**Database:** `railway`")
w(f"**Alembic head(s) in DB:** `{', '.join(alembic_heads) if alembic_heads else 'NONE — alembic_version table empty or missing'}`")
w(f"**Expected Alembic head:** `{EXPECTED_HEAD}`")
if set(alembic_heads) == {EXPECTED_HEAD}:
    w(f"**Migration status:** ✅ Up to date")
elif len(alembic_heads) > 1:
    w(f"**Migration status:** ⚠️ Multiple heads — branch merge may be incomplete")
else:
    w(f"**Migration status:** ❌ Mismatch — DB does not match latest migration")
w(f"")
w(f"---")
w(f"")

# Table-level summary
w(f"## Tables Overview")
w(f"")
w(f"| Table | Rows | In SQLAlchemy Model |")
w(f"|-------|------|---------------------|")
for t in sorted(live_tables):
    in_model = "✅" if t in model_tables else "❌ **ORPHAN**"
    rows = table_data[t]["total"]
    w(f"| `{t}` | {rows:,} | {in_model} |")
w(f"")

# Tables in model but not in DB
missing_from_db = model_tables - live_tables
if missing_from_db:
    w(f"**Tables defined in SQLAlchemy but NOT in production DB:**")
    for t in sorted(missing_from_db):
        w(f"- `{t}` — ❌ MISSING FROM DATABASE")
    w(f"")

w(f"---")
w(f"")

# Per-table detail
for table in sorted(live_tables):
    d = table_data[table]
    cols, pks, fks, idxs = d["cols"], d["pks"], d["fks"], d["idxs"]
    total, null_stats = d["total"], d["null_stats"]
    col_names, sample_rows = d["col_names"], d["sample_rows"]

    w(f"## Table: `{table}`")
    w(f"")
    w(f"**Row count:** {total:,}")
    w(f"")

    # Schema info
    w(f"### Columns")
    w(f"")
    w(f"| Column | DB Type | Nullable | Default | NULL% | Empty% |")
    w(f"|--------|---------|----------|---------|-------|--------|")
    for col in cols:
        col_name, data_type, udt_name, char_max_len, is_nullable, col_default = col
        nullable_str = "YES" if is_nullable == "YES" else "NO"
        default_str = col_default or ""
        # Truncate long defaults
        if len(default_str) > 40:
            default_str = default_str[:40] + "…"
        db_type_str = data_type
        if data_type == "character varying" and char_max_len:
            db_type_str = f"VARCHAR({char_max_len})"
        elif data_type == "USER-DEFINED":
            db_type_str = f"ENUM({udt_name})"

        ns = null_stats.get(col_name, {})
        null_pct  = fmt_pct(ns.get("null_count", 0), total) if total > 0 else "N/A"
        empty_pct = fmt_pct(ns.get("empty_count", 0), total) if pg_type_category(data_type) == "text" and total > 0 else "—"
        w(f"| `{col_name}` | {db_type_str} | {nullable_str} | {default_str} | {null_pct} | {empty_pct} |")
    w(f"")

    # Primary keys
    w(f"**Primary Key:** {', '.join(f'`{pk}`' for pk in pks) if pks else '_(none)_'}")
    w(f"")

    # Foreign keys
    if fks:
        w(f"**Foreign Keys:**")
        for fk_col, fk_table, fk_col2, delete_rule in fks:
            w(f"- `{fk_col}` → `{fk_table}.{fk_col2}` (ON DELETE {delete_rule})")
        w(f"")

    # Indexes
    if idxs:
        w(f"**Indexes:**")
        for idx_name, is_unique, idx_cols in idxs:
            uniq = " UNIQUE" if is_unique else ""
            w(f"- `{idx_name}`{uniq} on ({', '.join(idx_cols)})")
        w(f"")

    # Schema drift check
    w(f"### Schema Drift")
    w(f"")
    live_cols_set  = {c[0] for c in cols}
    model_def      = MODEL_SCHEMA.get(table, {})
    model_cols_set = set(model_def.keys())
    drift_found    = False

    if table not in MODEL_SCHEMA:
        w(f"⚠️ **No SQLAlchemy model found for this table.** It may be an Alembic-managed-only table or legacy.")
        drift_found = True
    else:
        # Cols in DB but not in model
        extra_in_db = live_cols_set - model_cols_set
        if extra_in_db:
            for col in sorted(extra_in_db):
                w(f"- 🔴 **Column `{col}` exists in DB but NOT in SQLAlchemy model**")
            drift_found = True

        # Cols in model but not in DB
        missing_in_db = model_cols_set - live_cols_set
        if missing_in_db:
            for col in sorted(missing_in_db):
                w(f"- 🔴 **Column `{col}` defined in model but NOT in DB** — migration not run or column dropped")
            drift_found = True

        # Type mismatches (rough)
        for col in cols:
            col_name, data_type, udt_name, _, _, _ = col
            if col_name in model_def:
                model_type = model_def[col_name]["type"]
                if not type_compatible(data_type, udt_name, model_type):
                    w(f"- ⚠️ **Type mismatch `{col_name}`:** DB=`{data_type}/{udt_name}`, Model=`{model_type}`")
                    drift_found = True

        # Nullable mismatches (model says NOT NULL but DB allows NULL)
        for col in cols:
            col_name, data_type, udt_name, _, is_nullable, _ = col
            if col_name in model_def:
                model_nullable = model_def[col_name]["nullable"]
                db_nullable    = is_nullable == "YES"
                if not model_nullable and db_nullable:
                    w(f"- ⚠️ **Nullable mismatch `{col_name}`:** model=NOT NULL, DB=nullable")
                    drift_found = True

        # Columns with 100% NULL that look important
        if total > 0:
            for col in cols:
                col_name = col[0]
                ns = null_stats.get(col_name, {})
                if ns.get("null_count", 0) == total:
                    w(f"- 🟡 **Column `{col_name}` is 100% NULL** across all {total:,} rows (potentially unused)")
                    drift_found = True

    if not drift_found:
        w(f"✅ No drift detected. Live schema matches model definition.")
    w(f"")

    # Sample rows
    w(f"### Sample Data (up to 3 rows)")
    w(f"")
    if not sample_rows:
        w(f"_(table is empty)_")
    else:
        w(f"| " + " | ".join(col_names) + " |")
        w(f"|" + "|".join(["---"] * len(col_names)) + "|")
        for row in sample_rows:
            cells = [redact(col_names[i], row[i]) for i in range(len(col_names))]
            w(f"| " + " | ".join(str(c) for c in cells) + " |")
    w(f"")
    w(f"---")
    w(f"")

# ── Summary of Findings ────────────────────────────────────────────────────────
w(f"## Summary of Findings")
w(f"")
w(f"Ordered by importance:")
w(f"")

findings = []

# 1. Alembic head — most critical: if empty, NO migrations are tracked
if not alembic_heads:
    findings.append(
        "**[CRITICAL] `alembic_version` table is empty.** "
        "No migrations have ever been stamped in production. "
        "The database was likely bootstrapped via `Base.metadata.create_all()` or a manual SQL dump, "
        "bypassing Alembic entirely. This means we have no reliable record of which schema changes "
        "have been applied. Run `alembic stamp head` (after verifying the schema matches the expected state) "
        "to re-establish tracking — but verify carefully first."
    )
elif set(alembic_heads) != {EXPECTED_HEAD}:
    findings.append(f"**[CRITICAL] Alembic version mismatch.** DB reports `{'`, `'.join(alembic_heads)}`, expected `{EXPECTED_HEAD}`. Some migrations may not have run.")
elif len(alembic_heads) > 1:
    findings.append(f"**[HIGH] Multiple Alembic heads:** `{', '.join(alembic_heads)}`. Branch merge migration (`i9j0k1l2m3n4`) may not have fully resolved.")

# 2. Scan all tables for RED drift (columns missing from DB that model defines, or extra)
for table in sorted(live_tables):
    d = table_data[table]
    live_cols_set = {c[0] for c in d["cols"]}
    model_def = MODEL_SCHEMA.get(table, {})
    model_cols_set = set(model_def.keys())
    if not model_def:
        continue
    missing_in_db = model_cols_set - live_cols_set
    extra_in_db   = live_cols_set - model_cols_set
    for col in sorted(missing_in_db):
        findings.append(
            f"**[HIGH] `{table}.{col}`** is defined in the SQLAlchemy model but does NOT exist in the DB. "
            f"The migration that adds it may not have run (Alembic is untracked). "
            f"This will cause `AttributeError` or `column not found` errors at runtime if this column is accessed."
        )
    for col in sorted(extra_in_db):
        findings.append(
            f"**[MEDIUM] `{table}.{col}`** exists in the DB but is NOT declared in the SQLAlchemy model. "
            f"Either the model is outdated or this column is a leftover from a dropped migration."
        )

# 3. instagram columns drift — specific context
users_live = {c[0] for c in table_data.get("users", {}).get("cols", [])}
ig_still_in_db = {"instagram_access_token", "instagram_user_id"} & users_live
if ig_still_in_db:
    findings.append(
        "**[MEDIUM] `users.instagram_access_token` and `users.instagram_user_id`** still exist in the DB "
        "(both 100% NULL). Migration `9185c895e607` was supposed to DROP them, but since Alembic is "
        "untracked in prod the migration never ran. These are dead columns. Safe to drop once Alembic "
        "tracking is re-established."
    )
# Note: instagram_username missing-from-DB is already caught by the generic drift scan above.

# 4. tables not in model
orphan_tables = live_tables - model_tables
if orphan_tables:
    for t in sorted(orphan_tables):
        findings.append(f"**[MEDIUM] Table `{t}`** exists in production DB but has no corresponding SQLAlchemy model class.")

# 5. tables in model but missing from DB
for t in sorted(missing_from_db):
    findings.append(f"**[HIGH] Table `{t}`** is defined in SQLAlchemy models but does NOT exist in production DB.")

# 6. Notable data quality issues on populated tables
for table in sorted(live_tables):
    d = table_data[table]
    total = d["total"]
    if total == 0:
        continue
    for col in d["cols"]:
        col_name = col[0]
        ns = d["null_stats"].get(col_name, {})
        if ns.get("null_count", 0) == total and col_name not in ("updated_at",):
            findings.append(f"**[LOW] `{table}.{col_name}`** is 100% NULL ({total:,} rows). Possibly never populated — consider dropping or deferring.")
        # 100% empty string
        elif ns.get("empty_count", 0) == total and col_name not in ("updated_at",):
            findings.append(f"**[LOW] `{table}.{col_name}`** is 100% empty string ({total:,} rows). Column exists but is never written.")

# 7. Empty tables
for table in sorted(live_tables):
    if table_data[table]["total"] == 0:
        findings.append(f"**[LOW] Table `{table}`** has 0 rows. Likely feature not yet used in production.")

# 8. cost_breakdown type note
pa = table_data.get("prysm_analyses", {})
pa_col_map = {c[0]: c for c in pa.get("cols", [])}
if "cost_breakdown" in pa_col_map:
    pg_type = pa_col_map["cost_breakdown"][1]
    if "jsonb" in pg_type.lower():
        findings.append(
            "**[INFO] `prysm_analyses.cost_breakdown`** is stored as `JSONB` in the DB "
            "but the SQLAlchemy model declares it as `JSON`. JSONB is strictly better "
            "(binary, indexed). Either update the model to use `JSONB` explicitly, or leave as-is — "
            "SQLAlchemy's `JSON` type works with JSONB columns on PostgreSQL."
        )

# 9. share_id width check
if "share_id" in pa_col_map:
    col = pa_col_map["share_id"]
    max_len = col[3]
    if max_len and int(max_len) < 32:
        findings.append(f"**[MEDIUM] `prysm_analyses.share_id`** is VARCHAR({max_len}) in DB but migration `f6a7b8c9d0e1` should have widened it to VARCHAR(32). Check if that migration ran.")

if not findings:
    findings.append("No significant issues found.")

for i, f in enumerate(findings, 1):
    w(f"{i}. {f}")

w(f"")
w(f"---")
w(f"_Audit script: `scripts/db_audit/run_audit.py` | Read-only, no writes performed._")

report = "\n".join(lines)
with open(OUT, "w") as fh:
    fh.write(report)
print(f"Report written to: {OUT}")
print(f"Total findings: {len(findings)}")
