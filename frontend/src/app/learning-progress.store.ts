import { inject, Injectable, signal } from '@angular/core';
import { LearnerProfile, LearningApiService, TopicProgressPayload } from './learning-api.service';

export interface TopicProgress {
  completedStepIds: string[];
  reviewPlans: ReviewPlan[];
  lastStepId: string | null;
  lastVisitedAt: number;
  quizAttempts: number;
  lastQuizScore: number | null;
  lastQuizAt: number;
}

export interface ReviewPlan {
  stepId: string;
  heading: string;
  stage: 0 | 1 | 2;
  dueAt: number;
  // Başarıya göre 1/3/7 günlük temel planı ölçekler. Değerler sınırlı
  // tutulur; kısa derslerde kontrolsüz uzun aralıklara çıkılmaz.
  ease: number;
  correctStreak: number;
  lastReviewedAt: number | null;
}

interface ProgressSnapshot {
  version: 4;
  clientId: string;
  topics: Record<string, TopicProgress>;
}

const STORAGE_KEY = 'konulab.learning-progress.v2';
const REVIEW_DELAYS_MS = [24 * 60 * 60 * 1_000, 3 * 24 * 60 * 60 * 1_000, 7 * 24 * 60 * 60 * 1_000];

@Injectable({ providedIn: 'root' })
export class LearningProgressStore {
  private readonly api = inject(LearningApiService);
  readonly snapshot = signal<ProgressSnapshot>(this.restore());
  readonly profile = signal<LearnerProfile | null>(null);
  readonly syncState = signal<'initializing' | 'ready' | 'offline'>('initializing');
  private readonly pendingTopicIds = new Set<string>();
  private readonly syncingTopicIds = new Set<number>();
  private readonly queuedTopicProgress = new Map<number, TopicProgress>();
  private readonly retryScheduledTopicIds = new Set<number>();

  constructor() {
    this.bootstrap();
  }

  get clientId(): string { return this.snapshot().clientId; }
  get profileId(): string | null { return this.profile()?.id ?? null; }

  topic(topicId: number): TopicProgress {
    return this.normaliseTopic(this.snapshot().topics[String(topicId)] ?? this.emptyTopic());
  }

  visit(topicId: number, stepId: string | null): void {
    this.updateTopic(topicId, (progress) => ({ ...progress, lastStepId: stepId, lastVisitedAt: Date.now() }));
  }

  setStepComplete(topicId: number, stepId: string, complete: boolean): ReviewPlan | null {
    const plan = this.topic(topicId).reviewPlans.find((item) => item.stepId === stepId) ?? null;
    this.updateTopic(topicId, (progress) => ({
      ...progress,
      completedStepIds: complete
        ? [...new Set([...progress.completedStepIds, stepId])]
        : progress.completedStepIds.filter((id) => id !== stepId),
    }));
    return plan;
  }

  recordQuiz(topicId: number, score: number, incorrectSteps: { stepId: string; heading: string }[], correctStepIds: string[]): void {
    this.updateTopic(topicId, (progress) => {
      const now = Date.now();
      const incorrectByStep = new Map(incorrectSteps.map((step) => [step.stepId, step]));
      const correctIds = new Set(correctStepIds.filter((stepId) => !incorrectByStep.has(stepId)));
      const existing = new Map(progress.reviewPlans.map((plan) => [plan.stepId, plan]));
      const reviewPlans = progress.reviewPlans
        .filter((plan) => !incorrectByStep.has(plan.stepId) && !correctIds.has(plan.stepId))
        .map((plan) => this.normalisePlan(plan));

      // Yanlış yanıt kısa aralığa geri döndürür. Aynı adımın tekrar eden
      // yanlışları kolaylık katsayısını düşürür; ilk yanlışta ise 1 gün kuralı
      // korunur.
      for (const step of incorrectByStep.values()) {
        const previous = existing.get(step.stepId);
        reviewPlans.push(this.resetReviewPlan(step, previous, now));
      }
      // Mevcut bir tekrar planındaki doğru quiz yanıtı, hatırlama kanıtı sayılır
      // ve bir sonraki aşamaya taşır. Planı doğrudan silmek yerine 1/3/7 akışı
      // tamamlanır.
      for (const stepId of correctIds) {
        const previous = existing.get(stepId);
        if (!previous) continue;
        const advanced = this.promotePlan(previous, now);
        if (advanced) reviewPlans.push(advanced);
      }
      return {
        ...progress,
        reviewPlans,
        quizAttempts: progress.quizAttempts + 1,
        lastQuizScore: score,
        lastQuizAt: now,
      };
    });
  }

  advanceReview(topicId: number, stepId: string): ReviewPlan | null {
    const plan = this.topic(topicId).reviewPlans.find((item) => item.stepId === stepId);
    if (!plan) return null;
    const next = this.promotePlan(plan, Date.now());
    this.updateTopic(topicId, (progress) => ({
      ...progress,
      reviewPlans: next
        ? progress.reviewPlans.map((item) => item.stepId === stepId ? next : item)
        : progress.reviewPlans.filter((item) => item.stepId !== stepId),
    }));
    return next;
  }

  prune(topicId: number, availableStepIds: string[]): void {
    const available = new Set(availableStepIds);
    this.updateTopic(topicId, (progress) => ({
      ...progress,
      completedStepIds: progress.completedStepIds.filter((id) => available.has(id)),
      reviewPlans: progress.reviewPlans.filter((plan) => available.has(plan.stepId)),
      lastStepId: progress.lastStepId && available.has(progress.lastStepId) ? progress.lastStepId : null,
    }));
  }

  private updateTopic(topicId: number, update: (current: TopicProgress) => TopicProgress): void {
    const current = this.snapshot();
    const next = { ...current, topics: { ...current.topics, [String(topicId)]: update(this.topicFrom(current, topicId)) } };
    this.snapshot.set(next);
    this.persist(next);
    if (this.profile()) this.queueTopicSync(topicId, next.topics[String(topicId)]);
    else this.pendingTopicIds.add(String(topicId));
  }

  private topicFrom(snapshot: ProgressSnapshot, topicId: number): TopicProgress {
    return this.normaliseTopic(snapshot.topics[String(topicId)] ?? this.emptyTopic());
  }

  private emptyTopic(): TopicProgress {
    return { completedStepIds: [], reviewPlans: [], lastStepId: null, lastVisitedAt: 0, quizAttempts: 0, lastQuizScore: null, lastQuizAt: 0 };
  }

  private restore(): ProgressSnapshot {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : null;
      if (parsed?.version === 4 && parsed.clientId && parsed.topics && typeof parsed.topics === 'object') {
        return { version: 4, clientId: parsed.clientId, topics: this.normaliseTopics(parsed.topics) };
      }
      if (parsed?.version === 3 && parsed.clientId && parsed.topics && typeof parsed.topics === 'object') {
        return { version: 4, clientId: parsed.clientId, topics: this.normaliseTopics(parsed.topics) };
      }
      if (parsed?.version === 2 && parsed.clientId && parsed.topics && typeof parsed.topics === 'object') {
        return { version: 4, clientId: parsed.clientId, topics: this.normaliseTopics(parsed.topics) };
      }
      if (parsed?.version === 1 && parsed.topics && typeof parsed.topics === 'object') {
        return {
          version: 4,
          clientId: crypto.randomUUID(),
          topics: Object.fromEntries(Object.entries(parsed.topics).map(([topicId, value]) => {
            const legacy = value as { completedStepIds?: string[]; reviewStepIds?: string[]; lastStepId?: string | null; lastVisitedAt?: number; quizAttempts?: number; lastQuizScore?: number | null };
            return [topicId, {
              completedStepIds: legacy.completedStepIds ?? [],
              reviewPlans: (legacy.reviewStepIds ?? []).map((stepId) => this.resetReviewPlan({ stepId, heading: 'Tekrar gereken adım' }, undefined, Date.now())),
              lastStepId: legacy.lastStepId ?? null,
              lastVisitedAt: legacy.lastVisitedAt ?? 0,
              quizAttempts: legacy.quizAttempts ?? 0,
              lastQuizScore: legacy.lastQuizScore ?? null,
              lastQuizAt: 0,
            }];
          })),
        };
      }
    } catch {
      // Bozuk veya erişilemeyen yerel depolama öğrenme ekranını engellemez.
    }
    return { version: 4, clientId: crypto.randomUUID(), topics: {} };
  }

  private persist(snapshot: ProgressSnapshot): void {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    } catch {
      // Gizli mod/depolama kotası durumunda ilerleme yalnızca açık oturumda kalır.
    }
  }

  renameProfile(displayName: string, onDone: (success: boolean) => void): void {
    const profile = this.profile();
    if (!profile) {
      onDone(false);
      return;
    }
    this.api.updateProfile(profile.id, this.clientId, displayName).subscribe({
      next: (updated) => {
        this.profile.set(updated);
        onDone(true);
      },
      error: () => onDone(false),
    });
  }

  private bootstrap(): void {
    this.api.bootstrapProfile(this.clientId, this.toPayloadMap(this.snapshot().topics)).subscribe({
      next: (profile) => {
        const current = this.snapshot();
        const topics = this.fromPayloadMap(profile.progress);
        // İlk API yanıtı gelirken kullanıcı bir adımı işaretlerse bu yeni işlem
        // yerel kopyadan alınır ve hemen ardından SQLite'a yazılır.
        for (const topicId of this.pendingTopicIds) topics[topicId] = current.topics[topicId];
        const next = { version: 4 as const, clientId: current.clientId, topics };
        this.snapshot.set(next);
        this.persist(next);
        this.profile.set(profile);
        this.syncState.set('ready');
        for (const topicId of this.pendingTopicIds) this.queueTopicSync(Number(topicId), topics[topicId]);
        this.pendingTopicIds.clear();
      },
      error: () => this.syncState.set('offline'),
    });
  }

  private queueTopicSync(topicId: number, progress: TopicProgress): void {
    this.queuedTopicProgress.set(topicId, progress);
    this.flushTopicSync(topicId);
  }

  private flushTopicSync(topicId: number): void {
    const profile = this.profile();
    const progress = this.queuedTopicProgress.get(topicId);
    if (!profile || !progress || this.syncingTopicIds.has(topicId)) return;
    this.queuedTopicProgress.delete(topicId);
    this.syncingTopicIds.add(topicId);
    this.api.saveTopicProgress(profile.id, topicId, this.clientId, this.toPayload(progress)).subscribe({
      next: () => {
        this.syncingTopicIds.delete(topicId);
        if (this.queuedTopicProgress.has(topicId)) this.flushTopicSync(topicId);
        else this.syncState.set('ready');
      },
      error: () => {
        this.syncingTopicIds.delete(topicId);
        // Son değer bellekte ve localStorage'da kalır. Yerel API tekrar
        // erişilebilir olduğunda aynı en güncel değer yeniden denenir.
        this.queuedTopicProgress.set(topicId, progress);
        this.syncState.set('offline');
        if (!this.retryScheduledTopicIds.has(topicId)) {
          this.retryScheduledTopicIds.add(topicId);
          setTimeout(() => {
            this.retryScheduledTopicIds.delete(topicId);
            this.flushTopicSync(topicId);
          }, 5_000);
        }
      },
    });
  }

  private toPayloadMap(topics: Record<string, TopicProgress>): Record<string, TopicProgressPayload> {
    return Object.fromEntries(Object.entries(topics).map(([topicId, progress]) => [topicId, this.toPayload(progress)]));
  }

  private toPayload(progress: TopicProgress): TopicProgressPayload {
    return {
      completed_step_ids: progress.completedStepIds,
      review_plans: progress.reviewPlans,
      last_step_id: progress.lastStepId,
      last_visited_at: progress.lastVisitedAt,
      quiz_attempts: progress.quizAttempts,
      last_quiz_score: progress.lastQuizScore,
      last_quiz_at: progress.lastQuizAt,
    };
  }

  private fromPayloadMap(progress: Record<string, TopicProgressPayload>): Record<string, TopicProgress> {
    return Object.fromEntries(Object.entries(progress).map(([topicId, value]) => [topicId, this.normaliseTopic({
      completedStepIds: value.completed_step_ids ?? [],
      reviewPlans: (value.review_plans ?? []).map((plan) => this.normalisePlan(plan as ReviewPlan)),
      lastStepId: value.last_step_id ?? null,
      lastVisitedAt: value.last_visited_at ?? 0,
      quizAttempts: value.quiz_attempts ?? 0,
      lastQuizScore: value.last_quiz_score ?? null,
      lastQuizAt: value.last_quiz_at ?? 0,
    })]));
  }

  private normaliseTopics(topics: Record<string, TopicProgress>): Record<string, TopicProgress> {
    return Object.fromEntries(Object.entries(topics).map(([topicId, progress]) => [topicId, this.normaliseTopic(progress)]));
  }

  private normaliseTopic(progress: TopicProgress): TopicProgress {
    return {
      ...progress,
      lastQuizAt: Number.isFinite(progress.lastQuizAt) ? progress.lastQuizAt : 0,
      reviewPlans: (progress.reviewPlans ?? []).map((plan) => this.normalisePlan(plan)),
    };
  }

  private normalisePlan(plan: ReviewPlan): ReviewPlan {
    return {
      ...plan,
      ease: Math.min(1.45, Math.max(.8, Number.isFinite(plan.ease) ? plan.ease : 1)),
      correctStreak: Math.max(0, Number.isFinite(plan.correctStreak) ? plan.correctStreak : 0),
      lastReviewedAt: typeof plan.lastReviewedAt === 'number' && Number.isFinite(plan.lastReviewedAt) ? plan.lastReviewedAt : null,
    };
  }

  private resetReviewPlan(step: { stepId: string; heading: string }, previous: ReviewPlan | undefined, now: number): ReviewPlan {
    const ease = Math.max(.8, (previous?.ease ?? 1) - (previous ? .15 : 0));
    return {
      stepId: step.stepId,
      heading: step.heading,
      stage: 0,
      dueAt: now + this.delayFor(0, ease),
      ease,
      correctStreak: 0,
      lastReviewedAt: now,
    };
  }

  private promotePlan(plan: ReviewPlan, now: number): ReviewPlan | null {
    const normalised = this.normalisePlan(plan);
    const nextStage = normalised.stage + 1;
    if (nextStage > 2) return null;
    const ease = Math.min(1.45, normalised.ease + .1);
    return {
      ...normalised,
      stage: nextStage as 1 | 2,
      dueAt: now + this.delayFor(nextStage, ease),
      ease,
      correctStreak: normalised.correctStreak + 1,
      lastReviewedAt: now,
    };
  }

  private delayFor(stage: number, ease: number): number {
    return Math.round(REVIEW_DELAYS_MS[stage] * ease);
  }
}
