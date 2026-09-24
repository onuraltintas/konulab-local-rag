from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Topic


TOPICS = [
    ("nlp", "Doğal Dil İşleme", "Doğal dil işleme öğrenme alanı", None, 1),
    ("normalization", "Metin Normalizasyonu", "Karakter, yapısal ve içerik normalizasyonu", "nlp", 10),
    ("tokenization", "Tokenizasyon", "Sözcük, cümle, n-gram ve alt sözcük yöntemleri", "nlp", 20),
    ("subword-tokenization", "Alt Sözcük Tokenizasyonu", "BPE, WordPiece ve Unigram LM", "tokenization", 10),
    ("vectorization", "Metin Vektörleştirme", "One-hot, BoW ve TF-IDF", "nlp", 30),
    ("classical-nlp", "Klasik NLP", "Naive Bayes, lojistik regresyon ve kümeleme", "nlp", 40),
    ("neural-networks", "Sinir Ağları", "Yapay sinir ağları ve derin öğrenme", "nlp", 50),
]


def seed_topics(database: Session) -> None:
    if database.scalar(select(Topic.id).limit(1)) is not None:
        return

    by_slug: dict[str, Topic] = {}
    for slug, title, description, parent_slug, order_index in TOPICS:
        topic = Topic(
            slug=slug,
            title=title,
            description=description,
            parent=by_slug.get(parent_slug) if parent_slug else None,
            order_index=order_index,
        )
        database.add(topic)
        database.flush()
        by_slug[slug] = topic
    database.commit()
