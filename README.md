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
uv sync --extra embeddings-cpu --extra conversion
export EMBEDDING_DEVICE=cpu
uv run uvicorn app.main:app --reload --port 8000
```

Bu yol CPU PyTorch kullanır; GX10 üzerinde üretim/indeksleme yolu değildir.
Linux'ta bağımsız metin/OCR kontrolleri için `poppler-utils`, `tesseract-ocr`,
`tesseract-ocr-tur` ve `tesseract-ocr-eng` paketlerini de kurun; GPU imajı bunları içerir.

## Temiz kurulum ve veri yerleşimi

Proje ders kitabı, örnek ders veya hazır kullanıcı verisi içermez. İlk çalıştırmada
boş SQLite şeması ve varsayılan model ayarları oluşturulur. İlk tarayıcı açılışında
yeni bir yerel profil oluşur; dersler ve konular ancak kaynak yükleyip onayladığınızda eklenir.

- `backend/app/`: uygulama kodu.
- `frontend/src/`: Angular arayüzü.
- `backend/tests/` ve `frontend/e2e/`: sentetik verilerle çalışan otomatik regresyon testleri.
- `backend/scripts/`: genel değerlendirme ve bakım araçları; kişisel değerlendirme verileri haricen sağlanır.
- `backend/data/`: Docker çalışmasında oluşan SQLite, kaynaklar ve dönüşüm çıktıları; Git'e alınmaz.
- Qdrant indeksleri ve embedding model önbelleği ayrı Docker volume'larında tutulur.

Backend'i yerel terminalden çalıştırırken önce `cd backend` kullanın; böylece
proje kökünde ikinci bir `data/` veritabanı oluşmaz. İndirilen ders dosyalarını ve
test raporlarını kaynak kodun içine koymayın. `node_modules` ve `.venv` kurulu
geliştirme bağımlılıklarıdır; uygulama verisi değildir.

### Otomatik testler

Backend testlerini GPU ortamında, gerçek veritabanından ayrı çalıştırın:

```bash
docker compose -f compose.yaml -f compose.gpu.yaml run --rm --no-deps \
  -v "$PWD/backend/tests:/app/tests:ro" \
  -e DATABASE_URL=sqlite:////tmp/konulab-tests.db \
  -e STORAGE_PATH=/tmp/konulab-test-sources \
  api python -m pytest tests -q
```

Tarayıcı regresyonları için `frontend` klasöründe `npm run e2e` kullanın. Bu
testler API yanıtlarını taklit eder; gerçek ders veya kullanıcı verisi oluşturmaz.

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

PDF tabloları hücre kapsamı ve kaynak metin eşleşmesiyle denetlenir. Şüpheli
tablolar kaynak PDF'den kırpılmış görüntüleriyle seçili görsel modele sırayla
otomatik gönderilir. Model çıktısında hücre kapsamı, birleşmeler, kaynak kelimeler
ve sayılar denetlenir; yapısal hata varsa bir kez yeniden denenir. Modelin güven
puanı tek başına doğruluk sayılmaz. Öneri ayrı saklanır; özgün tablo ve kaynak
görüntü kullanıcı onayına kadar korunur. Tam doğrulanamayan öneriler açıkça
işaretlenir ve toplu kabul edilmez. Her şüpheli tablo için öneriyi kabul etmek
veya özgün kaynağı korumak seçilmeden öğrenme haritasına yayınlama açılmaz.
Görsel model erişilemiyorsa kaynak kaybolmaz; yeniden deneme yapılabilir.

İlk öneride kaynak eşleşmesi belirsizse ikinci aşama otomatik çalışır: şüpheli
sözcükler PDF'nin özgün koordinatlarından 576 DPI kırpılır; uyuşmazlıkta 720 DPI
ile bir kez daha okunur. Önceki yanlış okuma modele gösterilmez. Düzeltme yalnız
bağımsız PDF metniyle görsel okuma uzlaşırsa öneriye uygulanır. Görsel model iki
denemede de uzlaşamazsa, Türkçe Tesseract OCR'ın en az %85 güvenli okuması PDF
metniyle birebir eşleştiğinde alternatif kanıt olarak kullanılabilir. Hangi kanıtla
düzeltildiği günlüğe yazılır; yalnız modelin kendi güven puanıyla karar verilmez. Hücre konumları ve
sayılar yeniden denetlenir. Tablo bütünü de yüksek çözünürlükte başlık/birleşme
açısından kontrol edilir. Kaynakta doğrulanan çevre başlıkları ayrı tablo bağlamı
olarak korunur. İş başına en fazla 16 şüpheli sözcük incelenir; kalanlar insan
incelemesinde kalır. Metin katmanı/koordinat bulunamayan taramalarda veya model
yanıt vermediğinde doğrulama başarısı iddia edilmez. İnceleme ekranındaki
“Şüpheli hücreleri yüksek çözünürlükte kontrol et” düğmesi aynı akışı mevcut
öneride yeniden çalıştırır. Kırpımlar ve değişiklik günlüğü kaynak sürümünün
`assets-v.../refinement-.../` klasöründe, özet SQLite'daki tablo önerisinde saklanır.

Diğer tablo veya görseller otomatik olarak modele gönderilmez. Kullanıcı
inceleme ekranında tek tek, seçerek veya tamamı için vision destekli yerel modeli
(`qwen3.8:27b` varsayılanı) çalıştırabilir. Açıklama taslak olarak kalır;
Markdown'a ancak kullanıcı kabul ederse eklenir. Kabul edilen görsel, Docling'in
orijinal yer işaretine göreli `assets/...` bağlantısıyla; tablo açıklaması ise
yapısal Markdown tablosunun hemen altında yazılır. Görsel açıklaması ana öğe ve
ilişkiyi anlatan 2–3 kısa cümle hedefler (350 karakter hedefi, 450 karakter kesin
sınırı); okunamayan içerik tahmin edilmez. Açıklama taslağı aynı kaynak görselle
ikinci model çağrısında nesne/panel ilişkileri açısından kontrol edilir; bu kontrol
insan onayının yerini almaz ve görsel başına iki çağrı gerektirir. Görsel model ayarları bulut sağlayıcıya
yöneltilmişse bu görüntüler de o sağlayıcıya gönderilir. Açıklama hazırlanırken
yayınlama kapalıdır. Bu sayede görsel bağlamı ancak kullanıcı görebildikten sonra
konu analizi ve indeksleme akışına girer.

### Dönüşüm kalite değerlendirmesi

Gerçek belge değerlendirmeleri isteğe bağlıdır; kitaplar veya kişisel belgeler
depoya eklenmez. Kendi manifest'inizi (bir JSON listesinde `name`, göreli `source`
ve `expectations` alanları) ve kaynak klasörünüzü açıkça verin:

```bash
python scripts/evaluate_conversion_quality.py \
  --manifest /inputs/manifest.json --source-root /inputs
```

Komut backend ortamında çalışır; Docker kullanıyorsanız harici kaynak klasörünü
salt okunur bağlayın. Manifest verilmeden gerçek belge değerlendirmesi başlatılmaz.
Otomatik testler ise büyük belge dosyalarına ihtiyaç duymadan çalışır.

## İsteğe bağlı bağlam ve matematik gösterimi

Konu taslağını onaylamadan önce **Yapay zekâ ile arama bağlamını zenginleştir**
seçeneği açılabilir; varsayılan kapalıdır. Seçili metin modeli her parçaya kısa
arama bağlamı üretir. Kaynak dosyası ve `DocumentSection.content` değişmez;
bağlam, model/sağlayıcı, istem sürümü ve kaynak parmak izi SQLite'da ayrı saklanır.
Bağlam yalnız dense/BM25 arama girdisine eklenir, alıntı olarak kullanılmaz.
Model hatasında özgün içerikle devam edilir. Token bütçesine sığmayan bağlam
atlanır; kaynak metin kesilmez. Tamamlanma mesajı kullanılan bağlam sayısını bildirir.
Bu seçenek daha uzun indeksleme süresi gerektirir ve her belgede kalite artışı garanti etmez.

İndekslenmiş belgede **Kaynaklar → belgeyi aç → Arama bağlamı** alanından
açıp kapatın ve **Ayarı uygula ve yeniden indeksle** düğmesine basın. İş durumu,
ilerleme ve hedef ayar SQLite'da saklanır; sayfa yenilendiğinde takip devam eder.
Yeni ayar başarılı indeks yazımından sonra geçerli olur. Kapatma kaynak metni,
konu eşleşmelerini veya öğrenme ilerlemesini değiştirmez; ayrı bağlam önbelleğini
silmez. Tekrar açıldığında kaynak/model/istem değişmemişse önbellek kullanılır.
`/reindex` ve `/rechunk-reindex` yolları da bağlamı denetler/yeniden üretir.
Sunucu yeniden başlarken yarım işler başarısız/yeniden denenebilir olarak işaretlenir.
Bu yerel iş yürütücüsü tek API süreci içindir; çoklu worker dağıtımı desteklenmez.

PDF dönüşümünde Docling formül zenginleştirmesi açıktır; ilk kullanımda
CodeFormulaV2 modeli indirilir ve model önbelleğinde tutulur. Okunamayan formüller
inceleme uyarısına dönüşür. Öğrenme, sohbet, özet, quiz, kaynak ve Markdown
inceleme alanları `$…$`, `$$…$$`, `\\(…\\)`, `\\[…\\]` ifadelerini KaTeX ile
gösterir. Kod blokları korunur; kaynak HTML çalıştırılmaz. KaTeX ve fontları
uygulamaya dahildir, gösterim için CDN/internet gerekmez. Formülün çizilmesi
matematiksel doğruluğunun doğrulandığı anlamına gelmez.

PDF formülleri, yüksek çözünürlüklü kaynak kırpımını **taslağı görmeden** okuyan
seçili görsel modelle karşılaştırılır. LaTeX yapısı ve PDF metin katmanındaki
harf/rakam/işaret kapsamı ayrıca denetlenir. Uyuşmazlık, eksik kaynak konumu veya
model hatasında ifade değiştirilmez: **İnceleme gerekli** gösterilir. Kalite
panelinde özgün kırpım, çıkarılan formül ve bağımsız okuma aynı yerde incelenebilir.
PDF metin katmanı bazı matematik işaretlerini içermeyebilir; bu da ihtiyatlı bir
inceleme uyarısı doğurabilir. Uyarılı veya sonradan düzenlenen formüllerle yayın,
yalnız kullanıcının açık onayıyla yapılır; onay tam Markdown sürüm karmasına bağlanır.
Bu denetimler eşdeğerlik veya matematiksel doğruluk ispatı değildir.

### Gerçek modellerle uçtan uca doğrulama

Normal `npm run e2e` testleri hızlı ve izoledir. Gerçek model testi varsayılan
atlıdır; ayrı SQLite/kaynak klasörü ve ayrı Qdrant koleksiyonuyla çalışan API
hazırlayın. Test API'si `http://127.0.0.1:8002` üzerinde ve CORS izinleri
`http://127.0.0.1:4201` için açık olmalıdır. Kullanıcı veritabanını kullanmayın.
GPU test ortamında `scripts/create_formula_test_pdf.py --output /tmp/formulas.pdf`
ile küçük Bayes/kök formülü PDF'si üretilebilir; büyük test belgeleri depoya eklenmez.

Frontend klasöründe:

```bash
KONULAB_REAL_E2E=1 \
KONULAB_REAL_API=http://127.0.0.1:8002 \
KONULAB_REAL_PDF=/tmp/formulas.pdf \
npm run e2e -- real-model-learning.spec.ts --workers=1 --retries=0
```

Bu test API/model yanıtlarını taklit etmez; yalnız ağ hedefini ayrı API'ye yönlendirir.
PDF yükleme, formül denetimi/kaynak görüntüsü, açık onay, konu analizi, bağlam üretimi,
Qdrant araması, öğrenme ekranında formül çizimi, sonradan açma/kapatma ve kaynak
metinlerinin değişmemesi doğrulanır. Test verisi ayrı ortamda kalır; bu ortamı
doğrulama sonrasında kaldırın.

Uygulama ayrıntıları: [Docling formül zenginleştirmesi](https://docling-project.github.io/docling/usage/enrichments/)
ve [KaTeX güvenlik/gösterim seçenekleri](https://katex.org/docs/options).

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
