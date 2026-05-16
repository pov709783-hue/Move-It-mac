import sqlite3
import os
import time
from datetime import datetime, timedelta
import sys

# Get permanent application data directory
app_data_dir = os.path.join(os.path.expanduser('~'), 'AppData', 'Local', 'Move-It')
os.makedirs(app_data_dir, exist_ok=True)
DB_PATH = os.path.join(app_data_dir, "move_it_data.db")

def get_connection():
    """Get a thread-safe SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # Better concurrent access
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            date TEXT PRIMARY KEY,
            total_sitting_seconds INTEGER DEFAULT 0,
            total_prompts INTEGER DEFAULT 0,
            total_skips INTEGER DEFAULT 0,
            total_dances INTEGER DEFAULT 0,
            total_stretches INTEGER DEFAULT 0,
            longest_session_seconds INTEGER DEFAULT 0,
            total_calories INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS dance_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            average_score INTEGER DEFAULT 0,
            duration_seconds INTEGER DEFAULT 0,
            calories INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS stretch_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            date TEXT NOT NULL,
            average_score INTEGER DEFAULT 0,
            duration_seconds INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sitting_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            duration_seconds INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS excluded_apps (
            executable_name TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        
        CREATE TABLE IF NOT EXISTS license (
            id INTEGER PRIMARY KEY DEFAULT 1,
            license_key TEXT,
            status TEXT
        );
    """)
    conn.commit()
    
    # Try to add calories columns to existing tables
    try:
        conn.execute("ALTER TABLE daily_stats ADD COLUMN total_calories INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE dance_sessions ADD COLUMN calories INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
        
    conn.commit()
    conn.close()

def get_setting(key, default_value=None):
    """Get a setting from the database."""
    conn = get_connection()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row['value'] if row else default_value

def set_setting(key, value):
    """Save a setting to the database."""
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def set_license(key, status):
    """Save the license key and status."""
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO license (id, license_key, status) VALUES (1, ?, ?)", (key, status))
    conn.commit()
    conn.close()

def get_license():
    """Retrieve the saved license key and status."""
    conn = get_connection()
    row = conn.execute("SELECT license_key, status FROM license WHERE id = 1").fetchone()
    conn.close()
    if row:
        return {"key": row['license_key'], "status": row['status']}
    return None

def today_str():
    return datetime.now().strftime("%Y-%m-%d")

def _ensure_today(conn):
    """Make sure today's row exists in daily_stats."""
    conn.execute(
        "INSERT OR IGNORE INTO daily_stats (date) VALUES (?)",
        (today_str(),)
    )
    conn.commit()

# ─── Recording Functions ────────────────────────────────────────────

def record_sitting_seconds(seconds):
    """Add sitting time to today's total."""
    conn = get_connection()
    _ensure_today(conn)
    conn.execute(
        "UPDATE daily_stats SET total_sitting_seconds = total_sitting_seconds + ? WHERE date = ?",
        (seconds, today_str())
    )
    conn.commit()
    conn.close()

def record_sitting_session(duration_seconds):
    """Record a completed sitting session and update longest streak."""
    conn = get_connection()
    _ensure_today(conn)
    conn.execute(
        "INSERT INTO sitting_sessions (date, start_time, duration_seconds) VALUES (?, ?, ?)",
        (today_str(), datetime.now().isoformat(), duration_seconds)
    )
    # Update longest session if this one is longer
    conn.execute(
        """UPDATE daily_stats 
           SET longest_session_seconds = MAX(longest_session_seconds, ?) 
           WHERE date = ?""",
        (duration_seconds, today_str())
    )
    conn.commit()
    conn.close()

def record_prompt():
    """Increment today's prompt count."""
    conn = get_connection()
    _ensure_today(conn)
    conn.execute(
        "UPDATE daily_stats SET total_prompts = total_prompts + 1 WHERE date = ?",
        (today_str(),)
    )
    conn.commit()
    conn.close()

def record_skip():
    """Increment today's skip count."""
    conn = get_connection()
    _ensure_today(conn)
    conn.execute(
        "UPDATE daily_stats SET total_skips = total_skips + 1 WHERE date = ?",
        (today_str(),)
    )
    conn.commit()
    conn.close()

def record_dance(average_score, duration_seconds, calories=0):
    """Record a completed dance session."""
    conn = get_connection()
    _ensure_today(conn)
    conn.execute(
        "INSERT INTO dance_sessions (timestamp, date, average_score, duration_seconds, calories) VALUES (?, ?, ?, ?, ?)",
        (datetime.now().isoformat(), today_str(), average_score, duration_seconds, calories)
    )
    conn.execute(
        "UPDATE daily_stats SET total_dances = total_dances + 1, total_calories = total_calories + ? WHERE date = ?",
        (calories, today_str())
    )
    conn.commit()
    conn.close()

def record_stretch(average_score, duration_seconds):
    """Record a completed stretch session."""
    conn = get_connection()
    _ensure_today(conn)
    conn.execute(
        "INSERT INTO stretch_sessions (timestamp, date, average_score, duration_seconds) VALUES (?, ?, ?, ?)",
        (datetime.now().isoformat(), today_str(), average_score, duration_seconds)
    )
    # Handle schema evolution gracefully
    try:
        conn.execute("UPDATE daily_stats SET total_stretches = total_stretches + 1 WHERE date = ?", (today_str(),))
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE daily_stats ADD COLUMN total_stretches INTEGER DEFAULT 0")
        conn.execute("UPDATE daily_stats SET total_stretches = total_stretches + 1 WHERE date = ?", (today_str(),))
    conn.commit()
    conn.close()

# ─── Query Functions ────────────────────────────────────────────────

def get_today_stats():
    """Get today's summary."""
    conn = get_connection()
    _ensure_today(conn)
    row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (today_str(),)).fetchone()
    
    # Get today's dance scores
    dances = conn.execute(
        "SELECT average_score FROM dance_sessions WHERE date = ?", (today_str(),)
    ).fetchall()
    
    avg_score = int(sum(d['average_score'] for d in dances) / len(dances)) if dances else 0
    best_score = max((d['average_score'] for d in dances), default=0)
    
    # Handle potential missing total_stretches column
    total_stretches = 0
    if 'total_stretches' in dict(row):
        total_stretches = row['total_stretches']

    # Get today's stretch scores
    try:
        stretches = conn.execute(
            "SELECT average_score FROM stretch_sessions WHERE date = ?", (today_str(),)
        ).fetchall()
        avg_stretch_score = int(sum(s['average_score'] for s in stretches) / len(stretches)) if stretches else 0
    except sqlite3.OperationalError:
        avg_stretch_score = 0

    # Handle potential missing total_calories column
    total_calories = 0
    if 'total_calories' in dict(row):
        total_calories = row['total_calories']

    conn.close()
    return {
        "date": row['date'],
        "sitting_minutes": round(row['total_sitting_seconds'] / 60, 1),
        "prompts": row['total_prompts'],
        "skips": row['total_skips'],
        "dances": row['total_dances'],
        "stretches": total_stretches,
        "avg_score": avg_score,
        "best_score": best_score,
        "avg_stretch_score": avg_stretch_score,
        "longest_session_min": round(row['longest_session_seconds'] / 60, 1),
        "calories": total_calories
    }

def get_week_stats():
    """Get last 7 days of stats."""
    conn = get_connection()
    days = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (d,)).fetchone()
        
        dances = conn.execute(
            "SELECT average_score FROM dance_sessions WHERE date = ?", (d,)
        ).fetchall()
        avg_score = int(sum(x['average_score'] for x in dances) / len(dances)) if dances else 0
        
        # Add stretches if column exists
        stretches = row['total_stretches'] if row and 'total_stretches' in dict(row) else 0
        calories = row['total_calories'] if row and 'total_calories' in dict(row) else 0

        if row:
            days.append({
                "date": d,
                "label": (datetime.now() - timedelta(days=i)).strftime("%a"),
                "sitting_minutes": round(row['total_sitting_seconds'] / 60, 1),
                "dances": row['total_dances'],
                "stretches": stretches,
                "skips": row['total_skips'],
                "avg_score": avg_score,
                "calories": calories
            })
        else:
            days.append({
                "date": d,
                "label": (datetime.now() - timedelta(days=i)).strftime("%a"),
                "sitting_minutes": 0,
                "dances": 0,
                "stretches": 0,
                "skips": 0,
                "avg_score": 0,
                "calories": 0
            })
    conn.close()
    return days

def get_score_history(days=14):
    """Get all dance scores from the last N days for the line chart."""
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT timestamp, average_score FROM dance_sessions WHERE date >= ? ORDER BY timestamp",
        (cutoff,)
    ).fetchall()
    conn.close()
    return [{"timestamp": r['timestamp'][:16], "score": r['average_score']} for r in rows]

def get_streak():
    """Calculate consecutive days with at least 1 dance (including today)."""
    conn = get_connection()
    streak = 0
    for i in range(0, 365):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = conn.execute(
            "SELECT total_dances FROM daily_stats WHERE date = ?", (d,)
        ).fetchone()
        if row and row['total_dances'] > 0:
            streak += 1
        else:
            break
    conn.close()
    return streak

def get_monthly_heatmap():
    """Get last 30 days of activity for the heatmap."""
    conn = get_connection()
    days = []
    for i in range(29, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (d,)).fetchone()
        sitting = row['total_sitting_seconds'] if row else 0
        dances = row['total_dances'] if row else 0
        stretches = row['total_stretches'] if row and 'total_stretches' in dict(row) else 0
        total_activity = dances + stretches
        # Activity level: 0 = none, 1 = light, 2 = moderate, 3 = high
        level = 0
        if sitting > 0: level = 1
        if total_activity >= 1: level = 2
        if total_activity >= 3: level = 3
        days.append({
            "date": d,
            "day": (datetime.now() - timedelta(days=i)).strftime("%d"),
            "level": level,
            "sitting_min": round(sitting / 60, 1),
            "dances": dances,
            "stretches": stretches
        })
    conn.close()
    return days

def get_all_dashboard_data():
    """Bundle everything the dashboard needs in one call."""
    return {
        "today": get_today_stats(),
        "week": get_week_stats(),
        "score_history": get_score_history(),
        "streak": get_streak(),
        "heatmap": get_monthly_heatmap()
    }

# Initialize on import
init_db()
