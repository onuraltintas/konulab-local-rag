# KonuLab

Yerel, konu-tabanlı ders dokümanı öğrenme platformu. Angular arayüzü, FastAPI,
SQLite kaynak kaydı, `turkish-e5-large` embedding modeli, Qdrant ve Ollama
kullanır. Birincil hedef ortam ASUS Ascent GX10 / NVIDIA GB10'dur.

## ASUS GX10 GPU ile başlatma

Bu profil FastAPI ve embedding işlemini NVIDIA'nın CUDA 13.0 tabanlı PyTorch NGC
imajında çalıştırır. Ollama host üzerinde kalır; API Linux host ağı sayesinde
`127.0.0.1:11434` üzerinden ona erişir ve Ollama'yı yerel ağınıza açmanız gerekmez.

1. Ollama'yı başlatın ve modeli indirin:

   ```bash
   sudo systemctl start ollama
   ollama pull qwen3.8:27b
   ollama ps
   ```

2. NVIDIA NGC'ye kişisel API anahtarınızla giriş yapın. Kullanıcı adı
   `$oauthtoken`, parola NGC anahtarınızdır; anahtar proje dosyalarına yazılmaz.

   ```bash
   docker login nvcr.io
   ```

3. Qdrant ve GPU API'yi başlatın:

   ```bash
   docker compose -f compose.yaml -f compose.gpu.yaml up --build -d
   ```

4. GPU erişimini model indirmeden doğrulayın:

   ```bash
   curl http://localhost:8000/api/runtime
   ```

Beklenen değerler: `embedding_device: "cuda"`, `cuda_available: true` ve
`cuda_device_name: "NVIDIA GB10"`. İlk doküman yüklemesinde embedding modeli
`hf_cache` Docker volume'una indirilir; sonraki başlatmalarda yeniden indirilmez.

5. Servisleri tek seferde doğrulayın:

   ```bash
   bash scripts/preflight.sh
   ```

6. Ayrı bir terminalde arayüzü başlatın:

   ```bash
   cd frontend
   npm install
   npm start
   ```

Arayüz `http://localhost:4200`, API dokümantasyonu `http://localhost:8000/docs`,
Qdrant paneli ise `http://localhost:6333/dashboard` adresinde açılır.

NGC PyTorch imajı varsayılan olarak `25.10-py3` sürümüne sabitlenmiştir: CUDA
13.0.2 kullanır ve 580.65+ sürücü gerektirir; GX10'deki 580.173.02 sürücüsüyle
uyumludur. Farklı bir imajı denemek isterseniz `NGC_PYTORCH_IMAGE` ortam
değişkeniyle yalnızca compose build sırasında değiştirin.

## CPU geliştirme seçeneği

GPU konteynerini kullanmayacağınız geliştirme makinelerinde:

```bash
cd backend
uv sync --extra embeddings-cpu
export EMBEDDING_DEVICE=cpu
uv run uvicorn app.main:app --reload --port 8000
```

Bu yol CPU PyTorch kullanır; GX10 üzerinde üretim/indeksleme yolu değildir.

## Vektör indeksleme akışı

### Kullanıcı onaylı konu çıkarma

Arayüzde bir `.txt`, `.md` veya `.py` ders dokümanı seçildiğinde önce
`POST /api/documents/analyze` çağrılır. Qwen dokümanın başlıklarını ve bölümlerini
analiz ederek konu taslağı üretir; her bölümü `explanation`, `example`, `code` veya
`exercise` türünde işaretler. Bu aşamada doküman henüz Qdrant'a yazılmaz.

Kullanıcı önerilen konuları kontrol eder ve onaylayacaklarını seçer. Ardından
`POST /api/documents/{id}/approve-analysis` yalnızca seçilen konuları oluşturur ya
da ada göre mevcut konularla birleştirir, kaynak bölümlerini bağlar ve vektör indeksini
oluşturur. Böylece modelin önerisi konu ağacını kullanıcı onayı olmadan değiştiremez.

`POST /api/documents` `.txt`, `.md` veya `.py` dosyasını yapısal bölümlere ayırır.
Her bölümde başlık yolu içerikle birlikte `ytu-ce-cosmos/turkish-e5-large` ile
1024 boyutlu, normalize bir vektöre dönüştürülür ve Qdrant'a yazılır. Bir yükleme
başarısız olursa mevcut aktif sürüm korunur. Aynı mantıksal anahtarla gelen yeni
başarılı sürüm eskisini `replaced` olarak işaretler; arama API'si Qdrant sonucunu
SQL'deki aktif sürüm durumu ile tekrar doğruladığından, eski vektör fiziksel olarak
silinmesi gecikse dahi yanıt bağlamına giremez.

`POST /api/search` semantik arama için `query`, isteğe bağlı `topic_id` ve `limit`
alanlarını kabul eder. `POST /api/chat` ise aynı kaynakları Qwen'e bağlam olarak
verir ve yanıtta `[K1]` biçiminde kaynak atıflarıyla birlikte kullanılan bölümleri
döndürür. Her ilk soru bir sohbet başlatır; sonraki isteklerde `conversation_id`
gönderilirse son altı mesaj bağlama eklenir. Kullanıcı ve asistan mesajları, model
adı ve o yanıtta kullanılan kaynak künyelerinin anlık görüntüsü SQLite'a yazılır.
`GET /api/conversations` ile liste, `GET /api/conversations/{id}` ile tam geçmiş
alınır. Varsayılan 32K context, birleşik belleği model ve embedding işlemiyle
dengeli paylaşmak içindir.

### Model sağlayıcısı ayarları

`/ayarlar` sayfasından sohbet, konu analizi, quiz ve kalite görevlerinin metin
modeli seçilir. Ollama için API, yerelde indirilmiş modelleri `/api/tags` ile;
vLLM için çalışan sunucunun servis ettiği modelleri `/v1/models` ile listeler.
vLLM bir model klasörü tarayıcısı değildir: yeni temel model için vLLM sunucusu o
modelle başlatılmalıdır. Ayar kaydedilmeden önce seçili modelin listede olduğu ve
kısa bir üretim isteğine yanıt verdiği doğrulanır; başarısız deneme mevcut çalışan
ayarı değiştirmez.

vLLM'yi uygulama API'sinden farklı bir portta çalıştırın; örneğin `8001`:

```bash
vllm serve <model-kimligi> --port 8001
```

Embedding modeli bu ekranda değiştirilmez. Model değişimi yeni Qdrant koleksiyonu,
tüm kaynakların yeniden embedding'i ve doğrulamalı indeks geçişi gerektirir.

### Görsel destekli belge dönüşümü

PDF, DOCX, PPTX ve XLSX dosyaları için arayüz önce
`POST /api/documents/convert` çağrısını yapar. Orijinal dosya saklanır; Docling
başlık yapısını ve tabloları koruyarak ayrı bir Markdown inceleme sürümü üretir.
Metni seyrek PDF'lerde işlem ikinci kez OCR açık olarak yürütülür. Dönüşümün
sayfa, tablo, görsel ve OCR bilgisi kullanıcıya gösterilir.

Bu aşamadaki Markdown **Qdrant'a yazılmaz**. Kullanıcı tüm metni inceleyip
“Markdown'ı onayla ve öğrenme haritasına dönüştür” dediğinde
`POST /api/documents/{id}/publish-conversion` mevcut konu taslağı/onay/indeks
akışını başlatır. Böylece eksik veya hatalı dönüşümün öğrenme haritasını sessizce
değiştirmesi engellenir. Orijinal dosya ve Markdown, kaynak silinene kadar aynı
kalıcı sürüm klasöründe tutulur.

Korunan her tablo veya görsel otomatik olarak dışarı gönderilmez. Kullanıcı
inceleme ekranında tek tek, seçerek veya tamamı için vision destekli yerel modeli
(`qwen3.8:27b` varsayılanı) çalıştırabilir. Açıklama taslak olarak kalır;
Markdown'a ancak kullanıcı kabul ederse eklenir. Kabul edilen görsel, Docling'in
orijinal yer işaretine göreli `assets/...` bağlantısıyla; tablo açıklaması ise
yapısal Markdown tablosunun hemen altında yazılır. Açıklama hazırlanırken
yayınlama kapalıdır. Bu sayede görsel bağlamı ancak kullanıcı görebildikten sonra
konu analizi ve indeksleme akışına girer.

### Dönüşüm kalite değerlendirmesi

`backend/evals/conversion_quality_benchmark.json`, gerçek yerel örnekler için
insan tarafından onaylanmış minimum Markdown uzunluğu, sayfa, tablo ve görsel
eşiğini tutar; dosyaların kendisini depoya kopyalamaz. Dönüştürme davranışını
değiştiren her güncellemeden sonra şu komut çalıştırılmalıdır:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml exec -T api \
  python scripts/evaluate_conversion_quality.py --source-root /workspace
```

Yeni örnek önce elle incelenir; ardından manifest'e göreli yol ve yeterince
kararlı eşikler eklenir. Böylece OCR, tablo ve görsel içeren kaynaklar için
kalite gerilemesi ölçülebilir hale gelir.

## GX10 kaynak kullanımı

GB10'da CPU ve GPU 128 GB birleşik belleği paylaşır. Bu nedenle embedding batch
boyutu GPU profilinde 32 ile başlar; büyük Ollama bağlam pencereleri veya başka ağır
işler aynı anda çalışıyorsa batch'i düşürün. `nvidia-smi` içindeki toplam bellek
alanının “Not Supported” görünmesi bu iGPU mimarisinde beklenen bir davranıştır.
# Güvenli geri yükleme

Yedek arşivi SQLite verisini ve ham kaynakları içerir; Qdrant vektörleri
türetilmiş veridir ve geri yükleme sırasında baştan üretilir. Bu işlem mevcut
SQLite ve kaynakları değiştirir. Başlamadan önce profil penceresinden yeni bir
yedek oluşturun ve arşivi doğrulayın.

```bash
curl http://localhost:8000/api/admin/backups/ARSIV_ADI.tar.gz/restore-preview
curl -X PUT http://localhost:8000/api/admin/maintenance \
  -H 'Content-Type: application/json' \
  --data '{"enabled":true,"confirmation":"BAKIM_MODUNU_AC","reason":"Yedekten geri yükleme"}'
docker compose -f compose.yaml -f compose.gpu.yaml stop api
docker compose -f compose.yaml -f compose.gpu.yaml run --rm --no-deps api \
  python scripts/restore_backup.py \
  --archive /app/data/backups/ARSIV_ADI.tar.gz \
  --confirm-offline-restore
docker compose -f compose.yaml -f compose.gpu.yaml up -d api
```

Komut önce mevcut SQLite ve kaynakların `restore-safety-*` emniyet kopyasını
alır, arşivi yol geçişi ve SQLite bütünlüğü açısından denetler, ardından
Qdrant koleksiyonunu silip aktif kaynakları yeniden indeksler. Bir hata olursa
bakım modu açık kalır; indeks tamamlanmadan kullanıcı yazmaları tekrar açılmaz.
