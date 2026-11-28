"""Project-scoped original raster images, retained separately from interpretation."""
import hashlib

from fastapi import HTTPException, Response


class ImageCache:
    def __init__(self, database, project, budget=32_000_000):
        self.database, self.project, self.remaining = database, project, budget

    def get(self, url):
        with self.database() as conn:
            row = conn.execute('SELECT a.id,a.content,a.mime FROM collection_image_urls u JOIN collection_images a ON a.id=u.image_id AND a.project_id=u.project_id WHERE u.project_id=? AND u.url=?', (self.project,url)).fetchone()
        return (bytes(row['content']), row['mime'], row['id']) if row else None

    def put(self, url, content, mime):
        from content_media import image_type
        if len(content) > 8_000_000 or image_type(content) != mime:
            raise ValueError('图片类型或大小无效')
        key = hashlib.sha256(content).hexdigest()
        with self.database() as conn:
            exists = conn.execute('SELECT 1 FROM collection_images WHERE project_id=? AND id=?', (self.project,key)).fetchone()
            if not exists:
                if len(content) > self.remaining:
                    raise ValueError('本轮原图保存预算已用完')
                self.remaining -= len(content)
                conn.execute('INSERT INTO collection_images(project_id,id,mime,content) VALUES(?,?,?,?)', (self.project,key,mime,content))
            conn.execute('INSERT OR REPLACE INTO collection_image_urls(project_id,url,image_id) VALUES(?,?,?)', (self.project,url,key))
        return key


def initialize(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS collection_images(project_id INTEGER NOT NULL,id TEXT NOT NULL,mime TEXT NOT NULL,content BLOB NOT NULL,PRIMARY KEY(project_id,id))')
    conn.execute('CREATE TABLE IF NOT EXISTS collection_image_urls(project_id INTEGER NOT NULL,url TEXT NOT NULL,image_id TEXT NOT NULL,PRIMARY KEY(project_id,url))')


def mount(app, database, project_settings):
    @app.get('/api/projects/{project}/collection-images/{image_id}')
    def image(project: int, image_id: str):
        with database() as conn:
            project_settings(conn,project)
            row=conn.execute('SELECT content,mime FROM collection_images WHERE project_id=? AND id=?',(project,image_id)).fetchone()
        if row is None:
            raise HTTPException(404,'项目中找不到原图')
        return Response(bytes(row['content']),media_type=row['mime'],headers={'Cache-Control':'private, max-age=3600','X-Content-Type-Options':'nosniff'})
