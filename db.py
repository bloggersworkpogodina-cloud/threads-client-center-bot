import os
import aiosqlite

DB_PATH = os.getenv("DB_PATH", "bot.db")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                telegram_id INTEGER UNIQUE,
                invite_code TEXT UNIQUE NOT NULL,
                threads_username TEXT,
                telegram_link TEXT,
                topic_id INTEGER,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS threads_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                post_date TEXT NOT NULL,
                slot TEXT NOT NULL,
                body TEXT NOT NULL,
                sent_at TEXT,
                published INTEGER NOT NULL DEFAULT 0,
                UNIQUE(client_id, post_date, slot),
                FOREIGN KEY(client_id) REFERENCES clients(id)
            );

            CREATE TABLE IF NOT EXISTS analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                analytics_date TEXT NOT NULL,
                slot TEXT NOT NULL,
                views INTEGER,
                likes INTEGER,
                comments INTEGER,
                reposts INTEGER,
                followers_delta INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id, analytics_date, slot),
                FOREIGN KEY(client_id) REFERENCES clients(id)
            );

            CREATE TABLE IF NOT EXISTS message_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                client_message_id INTEGER,
                group_message_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(client_id) REFERENCES clients(id)
            );

            CREATE TABLE IF NOT EXISTS result_polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL,
                period_end TEXT NOT NULL,
                tg_transitions INTEGER,
                inquiries INTEGER,
                sales INTEGER,
                revenue REAL,
                answer TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(client_id, period_end),
                FOREIGN KEY(client_id) REFERENCES clients(id)
            );
            """
        )
        # Миграция существующей базы: добавляем ссылку на контент-план без удаления данных.
        cur = await db.execute("PRAGMA table_info(clients)")
        columns = {row[1] for row in await cur.fetchall()}
        if "content_plan_url" not in columns:
            await db.execute("ALTER TABLE clients ADD COLUMN content_plan_url TEXT")

        # Чистим старые дубли без удаления истории.
        cur = await db.execute(
            """
            SELECT LOWER(TRIM(threads_username)) AS key_name
            FROM clients
            WHERE threads_username IS NOT NULL
              AND TRIM(threads_username) != ''
              AND is_active = 1
            GROUP BY LOWER(TRIM(threads_username))
            HAVING COUNT(*) > 1
            """
        )
        duplicate_keys = [row[0] for row in await cur.fetchall()]

        for key_name in duplicate_keys:
            cur = await db.execute(
                """
                SELECT id, telegram_id, topic_id, content_plan_url
                FROM clients
                WHERE LOWER(TRIM(threads_username)) = ?
                  AND is_active = 1
                ORDER BY
                    CASE WHEN telegram_id IS NOT NULL THEN 1 ELSE 0 END DESC,
                    id DESC
                """,
                (key_name,),
            )
            rows = await cur.fetchall()
            keeper = rows[0]
            keeper_id = keeper[0]
            keeper_telegram_id = keeper[1]
            keeper_topic_id = keeper[2]
            keeper_content_plan = keeper[3]

            for row in rows[1:]:
                duplicate_id, telegram_id, topic_id, content_plan_url = row

                if keeper_telegram_id is None and telegram_id is not None:
                    await db.execute(
                        "UPDATE clients SET telegram_id = ? WHERE id = ?",
                        (telegram_id, keeper_id),
                    )
                    keeper_telegram_id = telegram_id

                if keeper_topic_id is None and topic_id is not None:
                    await db.execute(
                        "UPDATE clients SET topic_id = ? WHERE id = ?",
                        (topic_id, keeper_id),
                    )
                    keeper_topic_id = topic_id

                if not keeper_content_plan and content_plan_url:
                    await db.execute(
                        "UPDATE clients SET content_plan_url = ? WHERE id = ?",
                        (content_plan_url, keeper_id),
                    )
                    keeper_content_plan = content_plan_url

                await db.execute(
                    "UPDATE clients SET is_active = 0 WHERE id = ?",
                    (duplicate_id,),
                )

        await db.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_active_threads
            ON clients(LOWER(TRIM(threads_username)))
            WHERE is_active = 1
              AND threads_username IS NOT NULL
              AND TRIM(threads_username) != ''
            """
        )

        await db.commit()


async def add_client(name, invite_code, threads_username=None, telegram_link=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO clients(name, invite_code, threads_username, telegram_link)
            VALUES (?, ?, ?, ?)
            """,
            (name, invite_code, threads_username.strip().lstrip("@") if threads_username else None, telegram_link),
        )
        await db.commit()
        return cur.lastrowid


async def bind_client(invite_code, telegram_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            UPDATE clients
            SET telegram_id = ?
            WHERE invite_code = ? AND telegram_id IS NULL
            """,
            (telegram_id, invite_code),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_client_by_tg(telegram_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM clients WHERE telegram_id = ? AND is_active = 1",
            (telegram_id,),
        )
        return await cur.fetchone()


async def get_client(client_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
        return await cur.fetchone()


async def list_clients(active_only=True):
    sql = "SELECT * FROM clients"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY name"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql)
        return await cur.fetchall()


async def save_post(client_id, post_date, slot, body):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO threads_posts(client_id, post_date, slot, body)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id, post_date, slot)
            DO UPDATE SET body = excluded.body, sent_at = NULL
            """,
            (client_id, post_date, slot, body),
        )
        await db.commit()


async def get_posts(client_id, post_date):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM threads_posts
            WHERE client_id = ? AND post_date = ?
            ORDER BY slot
            """,
            (client_id, post_date),
        )
        return await cur.fetchall()


async def mark_posts_sent(client_id, post_date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE threads_posts
            SET sent_at = CURRENT_TIMESTAMP
            WHERE client_id = ? AND post_date = ?
            """,
            (client_id, post_date),
        )
        await db.commit()


async def mark_published(post_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE threads_posts SET published = 1 WHERE id = ?",
            (post_id,),
        )
        await db.commit()


async def save_analytics(client_id, analytics_date, slot, views, likes, comments, reposts, followers_delta):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO analytics(
                client_id, analytics_date, slot, views, likes, comments, reposts, followers_delta
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id, analytics_date, slot)
            DO UPDATE SET
                views = excluded.views,
                likes = excluded.likes,
                comments = excluded.comments,
                reposts = excluded.reposts,
                followers_delta = excluded.followers_delta
            """,
            (client_id, analytics_date, slot, views, likes, comments, reposts, followers_delta),
        )
        await db.commit()


async def get_day_analytics(client_id, analytics_date):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM analytics
            WHERE client_id = ? AND analytics_date = ?
            ORDER BY slot
            """,
            (client_id, analytics_date),
        )
        return await cur.fetchall()


async def save_result_poll(client_id, period_end, tg_transitions=None, inquiries=None, sales=None, revenue=None, answer=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO result_polls(
                client_id, period_end, tg_transitions, inquiries, sales, revenue, answer
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(client_id, period_end)
            DO UPDATE SET
                tg_transitions = excluded.tg_transitions,
                inquiries = excluded.inquiries,
                sales = excluded.sales,
                revenue = excluded.revenue,
                answer = excluded.answer
            """,
            (client_id, period_end, tg_transitions, inquiries, sales, revenue, answer),
        )
        await db.commit()


async def set_client_topic(client_id, topic_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clients SET topic_id = ? WHERE id = ?",
            (topic_id, client_id),
        )
        await db.commit()


async def get_client_by_topic(topic_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM clients WHERE topic_id = ? AND is_active = 1",
            (topic_id,),
        )
        return await cur.fetchone()


async def save_message_link(client_id, client_message_id=None, group_message_id=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO message_links(client_id, client_message_id, group_message_id)
            VALUES (?, ?, ?)
            """,
            (client_id, client_message_id, group_message_id),
        )
        await db.commit()


async def close_client(client_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clients SET is_active = 0 WHERE id = ?",
            (client_id,),
        )
        await db.commit()


async def set_content_plan_url(client_id, url):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE clients SET content_plan_url = ? WHERE id = ?",
            (url, client_id),
        )
        await db.commit()
