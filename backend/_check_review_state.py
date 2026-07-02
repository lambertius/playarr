import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.database import SessionLocal
from app.models import VideoItem
from app.ai.models import AIThumbnail, AISceneAnalysis, SceneAnalysisStatus
db = SessionLocal()
items = db.query(VideoItem).filter(VideoItem.review_status == 'needs_human_review', VideoItem.review_category == 'missing_artwork').limit(10).all()
print(f'Found {len(items)} items')
for v in items:
    ps = v.processing_state or {}
    sa_completed = ps.get('scenes_analyzed', {}).get('completed', False)
    analysis = db.query(AISceneAnalysis).filter(AISceneAnalysis.video_id == v.id, AISceneAnalysis.status == SceneAnalysisStatus.complete).first()
    thumb = db.query(AIThumbnail).filter(AIThumbnail.video_id == v.id, AIThumbnail.is_selected == True).first()
    print(f'  {v.id}: {v.review_reason} | sa={sa_completed} | analysis={analysis is not None} | thumb={thumb is not None}')
db.close()