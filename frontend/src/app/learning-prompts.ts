/** Arayüz eylemlerinin oluşturduğu, kullanıcı tarafından görünür sohbet istekleri. */

export function topicOverviewPrompt(topicTitle: string): string {
  return `${topicTitle} konusunu kaynaklardaki sırayla açıklar mısın?`;
}

export function topicSummaryPrompt(topicTitle: string): string {
  return `${topicTitle} konusunu yalnızca kaynaklara dayanarak ana fikirler, önemli kavramlar ve kısa örneklerle özetle.`;
}

export function topicPracticePrompt(topicTitle: string): string {
  return `${topicTitle} konusu için kaynaklara dayalı, kolaydan zora 5 alıştırma sorusu üret. Yanıtları en sonda kısa açıklamalarla ver.`;
}

export function studyStepExplainPrompt(topicTitle: string, stepHeading: string): string {
  return `${topicTitle} içindeki “${stepHeading}” öğrenme adımını öğrenmeme yardım edecek biçimde açıkla.`;
}

export function studyStepCheckPrompt(topicTitle: string, stepHeading: string): string {
  return `${topicTitle} içindeki “${stepHeading}” adımı için 3 kısa kontrol sorusu sor. Cevapları ben istedikten sonra açıkla.`;
}
