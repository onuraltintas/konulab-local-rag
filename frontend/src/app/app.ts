import { DatePipe, JsonPipe } from '@angular/common';
import { Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { DocumentIndexJob } from './learning-api.service';
import { NavigationEnd, Router, RouterLink, RouterLinkActive } from '@angular/router';
import { filter } from 'rxjs';
import { AuthSession, ChatResponse, ChatSource, ConversationDetail, DashboardReview, DashboardTopic, DocumentAnalysis, DocumentAsset, DocumentConversion, DocumentDeletionImpact, FeedbackKind, FeedbackReviewStatus, LearnerDashboard, LearningApiService, LessonMergePreview, LlmProvider, ProviderDiscovery, QualityFeedback, QualityInsights, QualityOverview, RuntimeModelSettings, SourceDocument, SourceViewer, SystemStatus, Topic, TopicApprovalDecision, TopicPrerequisiteSuggestion, TopicQuiz, TopicQuizQuestion, TopicStudyStep } from './learning-api.service';
import { LearningProgressStore } from './learning-progress.store';
import { MathTextDirective } from './math-text.directive';
import { studyStepCheckPrompt, studyStepExplainPrompt, topicOverviewPrompt, topicSummaryPrompt } from './learning-prompts';

type UploadQueueStatus = 'queued' | 'analyzing' | 'ready' | 'failed';

interface UploadQueueItem {
  id: string;
  file: File;
  status: UploadQueueStatus;
  progress: number;
  message: string;
  documentId?: string;
  kind: 'analysis' | 'conversion';
}

interface LessonGroup {
  id: string;
  title: string;
  documents: SourceDocument[];
}

@Component({
  selector: 'app-root',
  imports: [RouterLink, RouterLinkActive, JsonPipe, DatePipe, MathTextDirective],
  templateUrl: './app.html',
  styleUrl: './app.scss'
})
export class App {
  private readonly api = inject(LearningApiService);
  private readonly progress = inject(LearningProgressStore);
  private readonly router = inject(Router);
  private readonly feedbackStateStorageKey = 'konulab.feedback-state.v2';

  protected readonly topics = signal<Topic[]>([]);
  protected readonly documents = signal<SourceDocument[]>([]);
  protected readonly error = signal<string | null>(null);
  protected readonly selectedTopic = signal<Topic | null>(null);
  protected readonly chatDraft = signal('');
  protected readonly chatResponse = signal<ChatResponse | null>(null);
  protected readonly conversationId = signal<string | null>(null);
  protected readonly conversation = signal<ConversationDetail | null>(null);
  protected readonly chatError = signal<string | null>(null);
  protected readonly chatLoading = signal(false);
  protected readonly studyMode = signal<'learn' | 'summary' | 'chat' | 'practice'>('learn');
  protected readonly studySteps = signal<TopicStudyStep[]>([]);
  protected readonly studyStepIndex = signal(0);
  protected readonly studyLoading = signal(false);
  protected readonly studyError = signal<string | null>(null);
  protected readonly stepAssistantResponse = signal<ChatResponse | null>(null);
  protected readonly stepAssistantLoading = signal(false);
  protected readonly stepAssistantError = signal<string | null>(null);
  protected readonly summaryResponse = signal<ChatResponse | null>(null);
  protected readonly summaryLoading = signal(false);
  protected readonly summaryError = signal<string | null>(null);
  protected readonly quiz = signal<TopicQuiz | null>(null);
  protected readonly quizLoading = signal(false);
  protected readonly quizError = signal<string | null>(null);
  protected readonly quizAnswers = signal<Record<string, string>>({});
  protected readonly quizSubmitted = signal(false);
  protected readonly feedbackKeys = signal<string[]>(this.loadStoredFeedbackKeys());
  protected readonly weakSourceSectionIds = signal<string[]>([]);
  protected readonly profileDialogOpen = signal(false);
  protected readonly profileNameDraft = signal('');
  protected readonly account = signal<AuthSession | null>(this.api.session);
  protected readonly authMode = signal<'login' | 'register'>('login');
  protected readonly authUsername = signal('');
  protected readonly authPassword = signal('');
  protected readonly authLoading = signal(false);
  protected readonly authError = signal<string | null>(null);
  protected readonly systemStatus = signal<SystemStatus | null>(null);
  protected readonly systemStatusLoading = signal(false);
  protected readonly backupCreating = signal(false);
  protected readonly sourceViewer = signal<SourceViewer | null>(null);
  protected readonly sourceViewerLoading = signal(false);
  protected readonly sourceViewerError = signal<string | null>(null);
  protected readonly topicPrerequisites = signal<Topic[]>([]);
  protected readonly prerequisiteEditing = signal(false);
  protected readonly prerequisiteDraftIds = signal<number[]>([]);
  protected readonly prerequisiteSaving = signal(false);
  protected readonly prerequisiteSuggestions = signal<TopicPrerequisiteSuggestion[]>([]);
  protected readonly prerequisiteSuggesting = signal(false);
  protected readonly qualityOverview = signal<QualityOverview | null>(null);
  protected readonly qualityFeedback = signal<QualityFeedback[]>([]);
  protected readonly qualityFilter = signal<FeedbackReviewStatus | 'all'>('new');
  protected readonly qualityLoading = signal(false);
  protected readonly qualityError = signal<string | null>(null);
  protected readonly qualityReviewNotes = signal<Record<string, string>>({});
  protected readonly qualityInsights = signal<QualityInsights | null>(null);
  protected readonly qualityExporting = signal(false);
  protected readonly dashboard = signal<LearnerDashboard | null>(null);
  protected readonly dashboardLoading = signal(false);
  protected readonly dashboardError = signal<string | null>(null);
  private readonly pendingStudyStepId = signal<string | null>(null);
  protected readonly uploadQueue = signal<UploadQueueItem[]>([]);
  protected readonly uploadLoading = signal(false);
  protected readonly uploadError = signal<string | null>(null);
  protected readonly uploadSuccess = signal<string | null>(null);
  protected readonly documentAnalysis = signal<DocumentAnalysis | null>(null);
  protected readonly conversionReview = signal<DocumentConversion | null>(null);
  protected readonly visualAnalysisLoading = signal(false);
  protected readonly selectedAssetIds = signal<string[]>([]);
  protected readonly selectedConversionAssetId = signal<string | null>(null);
  protected readonly assetDownloadId = signal<string | null>(null);
  protected readonly selectedConversionAsset = computed(() => {
    const assets = this.conversionReview()?.assets ?? [];
    const selectedId = this.selectedConversionAssetId();
    return assets.find((asset) => asset.id === selectedId) ?? assets[0] ?? null;
  });
  protected readonly markdownEditing = signal(false);
  protected readonly markdownDraft = signal('');
  protected readonly markdownSaving = signal(false);
  protected readonly markdownRestoring = signal<number | null>(null);
  protected readonly documentDetails = signal<Record<string, DocumentAnalysis>>({});
  protected readonly documentConversions = signal<Record<string, DocumentConversion>>({});
  protected readonly expandedDocumentId = signal<string | null>(null);
  protected readonly expandedLessonId = signal<string | null>(null);
  protected readonly documentDetailLoading = signal<string | null>(null);
  protected readonly documentsToDelete = signal<SourceDocument[]>([]);
  protected readonly deletingDocumentId = signal<string | null>(null);
  protected readonly deletionImpact = signal<DocumentDeletionImpact | null>(null);
  protected readonly deletionImpactLoading = signal(false);
  protected readonly deletionImpactError = signal<string | null>(null);
  protected readonly selectedDocumentIds = signal<string[]>([]);
  protected readonly toast = signal<{ message: string; tone: 'success' | 'error' } | null>(null);
  protected readonly approvedProposalIds = signal<string[]>([]);
  protected readonly contextualEnrichment = signal(false);
  protected readonly indexJobs = signal<Record<string, DocumentIndexJob | null>>({});
  protected readonly indexDrafts = signal<Record<string, boolean>>({});
  protected readonly indexStarting = signal<string | null>(null);
  protected readonly formulaAcceptanceKey = signal<string | null>(null);
  private readonly indexTimers = new Map<string, number>();
  protected readonly proposalDrafts = signal<Record<string, { title: string; description: string }>>({});
  protected readonly lessonTitleDraft = signal('');
  protected readonly lessonMergePreview = signal<LessonMergePreview | null>(null);
  protected readonly lessonMergeChecking = signal(false);
  protected readonly approvalLoading = signal(false);
  protected readonly reanalyzeLoading = signal(false);
  protected readonly indexingDocumentId = signal<string | null>(null);
  protected readonly runtimeModelSettings = signal<RuntimeModelSettings | null>(null);
  protected readonly runtimeModelDraft = signal<RuntimeModelSettings | null>(null);
  protected readonly runtimeModelLoading = signal(false);
  protected readonly runtimeModelSaving = signal(false);
  protected readonly runtimeModelTesting = signal(false);
  protected readonly runtimeModelError = signal<string | null>(null);
  protected readonly runtimeModelTestMessage = signal<string | null>(null);
  protected readonly currentPage = signal<'home' | 'map' | 'sources' | 'quality' | 'settings'>('home');
  private analysisPollTimer: number | null = null;
  private uploadQueuePollTimer: number | null = null;
  private conversionReviewPollTimer: number | null = null;
  private toastTimer: number | null = null;
  private feedbackStateScope: string | null = null;
  protected readonly rootTopics = computed(() => this.topics().filter((topic) => topic.parent_id === null));
  protected readonly prerequisiteCandidates = computed(() => {
    const selectedId = this.selectedTopic()?.id;
    return this.topics().filter((topic) => topic.id !== selectedId).sort((left, right) => left.title.localeCompare(right.title, 'tr'));
  });
  protected readonly learnerProfile = this.progress.profile;
  protected readonly progressSyncState = this.progress.syncState;
  protected readonly activeDocuments = computed(() => this.documents().filter((document) => document.status === 'active'));
  protected readonly lessonGroups = computed<LessonGroup[]>(() => {
    const groups = new Map<string, LessonGroup>();
    for (const document of this.activeDocuments()) {
      const title = this.documentTitle(document);
      const id = title.toLocaleLowerCase('tr-TR').trim();
      const current = groups.get(id);
      if (current) current.documents.push(document);
      else groups.set(id, { id, title, documents: [document] });
    }
    return [...groups.values()];
  });
  protected readonly childTopics = computed(() => {
    const grouped = new Map<number, Topic[]>();
    for (const topic of this.topics()) {
      if (topic.parent_id === null) continue;
      grouped.set(topic.parent_id, [...(grouped.get(topic.parent_id) ?? []), topic]);
    }
    return grouped;
  });
  protected readonly currentStudyStep = computed(() => this.studySteps()[this.studyStepIndex()] ?? null);
  protected readonly currentTopicProgress = computed(() => {
    const topic = this.selectedTopic();
    const steps = this.studySteps();
    if (!topic) return { completed: 0, total: steps.length, percent: 0, reviewCount: 0 };
    const progress = this.progress.topic(topic.id);
    const completed = steps.filter((step) => progress.completedStepIds.includes(step.id)).length;
    return {
      completed,
      total: steps.length,
      percent: steps.length ? Math.round((completed / steps.length) * 100) : 0,
      reviewCount: progress.reviewPlans.length,
      complete: steps.length > 0 && completed === steps.length && progress.reviewPlans.length === 0,
    };
  });

  constructor() {
    inject(DestroyRef).onDestroy(() => { for (const timer of this.indexTimers.values()) window.clearTimeout(timer); });
    this.updateCurrentPage(this.router.url);
    this.router.events.pipe(filter((event): event is NavigationEnd => event instanceof NavigationEnd)).subscribe((event) => {
      this.updateCurrentPage(event.urlAfterRedirects);
    });
    effect(() => {
      if (this.currentPage() === 'home' && this.learnerProfile()) this.loadDashboard();
    });
    effect(() => {
      const profile = this.learnerProfile();
      if (profile && this.progressSyncState() === 'ready') this.loadPersistedFeedbackState(profile.id);
    });
    this.loadWorkspace();
  }

  protected openDashboardReview(review: DashboardReview): void {
    this.openDashboardTopic(review.topic_id, review.step_id);
  }

  protected openDashboardTopic(topicId: number, stepId: string | null = null): void {
    const topic = this.topics().find((item) => item.id === topicId);
    if (!topic) {
      this.showToast('Bu konu artık öğrenme haritasında bulunmuyor.', 'error');
      return;
    }
    this.pendingStudyStepId.set(stepId);
    this.router.navigate(['/harita']).then(() => this.selectTopic(topic));
  }

  protected dashboardQuizLabel(topic: DashboardTopic): string {
    return topic.last_quiz_score === null ? 'Henüz quiz yok' : `${topic.last_quiz_score}/3 doğru`;
  }

  protected dashboardDate(timestamp: number): string {
    return timestamp ? new Intl.DateTimeFormat('tr-TR', { dateStyle: 'medium' }).format(new Date(timestamp)) : '';
  }

  protected selectTopic(topic: Topic): void {
    this.selectedTopic.set(topic);
    this.chatResponse.set(null);
    this.chatError.set(null);
    this.conversationId.set(null);
    this.conversation.set(null);
    this.summaryResponse.set(null);
    this.summaryError.set(null);
    this.studyMode.set('learn');
    this.quiz.set(null);
    this.quizAnswers.set({});
    this.quizSubmitted.set(false);
    this.quizError.set(null);
    this.prerequisiteEditing.set(false);
    this.loadTopicPrerequisites(topic.id);
    this.loadTopicStudy(topic.id);
  }

  protected beginPrerequisiteEditing(): void {
    this.prerequisiteDraftIds.set(this.topicPrerequisites().map((topic) => topic.id));
    this.prerequisiteEditing.set(true);
  }

  protected suggestPrerequisites(): void {
    const topic = this.selectedTopic();
    if (!topic || this.prerequisiteSuggesting()) return;
    this.prerequisiteSuggesting.set(true);
    this.api.suggestTopicPrerequisites(topic.id).subscribe({
      next: (suggestions) => {
        this.prerequisiteSuggestions.set(suggestions);
        this.prerequisiteDraftIds.set([...new Set([...this.topicPrerequisites().map((item) => item.id), ...suggestions.map((item) => item.topic_id)])]);
        this.prerequisiteEditing.set(true);
        this.prerequisiteSuggesting.set(false);
      },
      error: (response) => { this.prerequisiteSuggesting.set(false); this.showToast(response.error?.detail ?? 'Öneriler hazırlanamadı.', 'error'); },
    });
  }

  protected togglePrerequisite(topicId: number, checked: boolean): void {
    this.prerequisiteDraftIds.update((ids) => checked ? [...new Set([...ids, topicId])] : ids.filter((id) => id !== topicId));
  }

  protected cancelPrerequisiteEditing(): void {
    this.prerequisiteEditing.set(false);
  }

  protected savePrerequisites(): void {
    const topic = this.selectedTopic();
    if (!topic || this.prerequisiteSaving()) return;
    this.prerequisiteSaving.set(true);
    this.api.updateTopicPrerequisites(topic.id, this.prerequisiteDraftIds()).subscribe({
      next: (prerequisites) => {
        this.topicPrerequisites.set(prerequisites);
        this.prerequisiteSaving.set(false);
        this.prerequisiteEditing.set(false);
        this.showToast('Konu ön koşulları kaydedildi.', 'success');
      },
      error: (response) => {
        this.prerequisiteSaving.set(false);
        this.showToast(response.error?.detail ?? 'Ön koşullar kaydedilemedi.', 'error');
      },
    });
  }

  protected updateChatDraft(value: string): void {
    this.chatDraft.set(value);
  }

  protected openProfileDialog(): void {
    this.profileNameDraft.set(this.learnerProfile()?.display_name ?? 'Yerel öğrenci');
    this.profileDialogOpen.set(true);
    this.authError.set(null);
    this.loadSystemStatus();
  }

  protected closeProfileDialog(): void {
    this.profileDialogOpen.set(false);
  }

  protected updateProfileName(value: string): void {
    this.profileNameDraft.set(value);
  }

  protected profileInitials(): string {
    const words = (this.learnerProfile()?.display_name ?? 'Yerel öğrenci').trim().split(/\s+/).filter(Boolean);
    return words.slice(0, 2).map((word) => word[0]).join('').toLocaleUpperCase('tr-TR') || 'Ö';
  }

  protected saveProfileName(): void {
    const name = this.profileNameDraft().trim();
    if (!name) {
      this.showToast('Profil adı boş olamaz.', 'error');
      return;
    }
    this.progress.renameProfile(name, (success) => {
      if (success) {
        this.profileDialogOpen.set(false);
        this.showToast('Profil adı SQLite’a kaydedildi.', 'success');
      } else {
        this.showToast('Profil adı şu an kaydedilemedi. Bağlantıyı kontrol et.', 'error');
      }
    });
  }

  protected setAuthMode(mode: 'login' | 'register'): void {
    this.authMode.set(mode);
    this.authError.set(null);
  }

  protected updateAuthField(field: 'username' | 'password', value: string): void {
    if (field === 'username') this.authUsername.set(value);
    else this.authPassword.set(value);
  }

  protected submitAuth(): void {
    const username = this.authUsername().trim();
    const password = this.authPassword();
    const profile = this.learnerProfile();
    if (!username || !password || this.authLoading()) return;
    this.authLoading.set(true);
    this.authError.set(null);
    const request = this.authMode() === 'register'
      ? this.api.register(username, this.profileNameDraft().trim() || username, password, profile?.id ?? null, this.progress.clientId)
      : this.api.login(username, password, profile?.id ?? null, this.progress.clientId);
    request.subscribe({
      next: (session) => {
        this.api.saveSession(session);
        this.account.set(session);
        this.authPassword.set('');
        this.authLoading.set(false);
        this.showToast('Hesap oturumu açıldı; yerel ilerlemen bu hesaba bağlandı.', 'success');
      },
      error: (response) => {
        this.authLoading.set(false);
        this.authError.set(response.error?.detail ?? 'Hesap işlemi tamamlanamadı.');
      },
    });
  }

  protected logout(): void {
    this.api.logout().subscribe({ complete: () => this.api.clearSession() });
    this.account.set(null);
    this.showToast('Oturum kapatıldı.', 'success');
  }

  protected loadSystemStatus(): void {
    if (this.systemStatusLoading()) return;
    this.systemStatusLoading.set(true);
    this.api.getSystemStatus().subscribe({
      next: (systemStatus) => {
        this.systemStatus.set(systemStatus);
        this.systemStatusLoading.set(false);
      },
      error: () => {
        this.systemStatus.set(null);
        this.systemStatusLoading.set(false);
      },
    });
  }

  protected createBackup(): void {
    if (this.backupCreating()) return;
    this.backupCreating.set(true);
    this.api.createBackup().subscribe({
      next: () => {
        this.backupCreating.set(false);
        this.showToast('Yedek oluşturuldu. Kaynaklar ve SQLite verisi birlikte saklandı.', 'success');
        this.loadSystemStatus();
      },
      error: (response) => {
        this.backupCreating.set(false);
        this.showToast(response.error?.detail ?? 'Yedek oluşturulamadı.', 'error');
      },
    });
  }

  protected startNewConversation(): void {
    this.conversationId.set(null);
    this.chatResponse.set(null);
    this.chatError.set(null);
    this.conversation.set(null);
    this.chatDraft.set('');
  }

  protected setStudyMode(mode: 'learn' | 'summary' | 'chat' | 'practice'): void {
    this.studyMode.set(mode);
    if (mode === 'chat' && !this.chatDraft().trim()) {
      const topic = this.selectedTopic();
      if (topic) this.chatDraft.set(topicOverviewPrompt(topic.title));
    }
  }

  protected generateTopicSummary(): void {
    const topic = this.selectedTopic();
    if (!topic || this.summaryLoading()) return;
    this.summaryLoading.set(true);
    this.summaryError.set(null);
    this.summaryResponse.set(null);
    // Özet ayrı bir çalışma çıktısıdır; önceki serbest sohbeti kirletmez.
    this.api.chat(topicSummaryPrompt(topic.title), topic.id, null).subscribe({
      next: (response) => {
        this.conversationId.set(response.conversation_id);
        this.summaryResponse.set(response);
        this.summaryLoading.set(false);
      },
      error: (response) => {
        this.summaryLoading.set(false);
        this.summaryError.set(response.error?.detail ?? 'Kaynaklı özet oluşturulamadı.');
      },
    });
  }

  protected continueSummaryInChat(): void {
    const response = this.summaryResponse();
    if (!response) return;
    this.studyMode.set('chat');
    this.chatResponse.set(response);
    this.chatDraft.set('');
    this.api.getConversation(response.conversation_id).subscribe({
      next: (conversation) => this.conversation.set(conversation),
    });
  }

  protected moveStudyStep(direction: -1 | 1): void {
    const next = this.studyStepIndex() + direction;
    if (next >= 0 && next < this.studySteps().length) {
      this.studyStepIndex.set(next);
      const topic = this.selectedTopic();
      const step = this.studySteps()[next];
      if (topic && step) this.progress.visit(topic.id, step.id);
      this.stepAssistantResponse.set(null);
      this.stepAssistantError.set(null);
    }
  }

  protected isCurrentStepComplete(): boolean {
    const topic = this.selectedTopic();
    const step = this.currentStudyStep();
    return Boolean(topic && step && this.progress.topic(topic.id).completedStepIds.includes(step.id));
  }

  protected toggleCurrentStepCompletion(): void {
    const topic = this.selectedTopic();
    const step = this.currentStudyStep();
    if (!topic || !step) return;
    const review = this.currentReviewPlan();
    if (review) {
      this.progress.setStepComplete(topic.id, step.id, true);
      const next = this.progress.advanceReview(topic.id, step.id);
      this.showToast(next ? `Tekrar tamamlandı. Sonraki tekrar ${this.reviewDueLabel(next)}.` : 'Tekrar planı tamamlandı; bu adım artık pekişti.', 'success');
      return;
    }
    const complete = !this.isCurrentStepComplete();
    this.progress.setStepComplete(topic.id, step.id, complete);
    this.showToast(complete ? 'Öğrenme adımı tamamlandı olarak kaydedildi.' : 'Öğrenme adımı tekrar açık olarak işaretlendi.', 'success');
  }

  protected currentReviewPlan(): { stepId: string; heading: string; stage: 0 | 1 | 2; dueAt: number; ease: number; correctStreak: number } | null {
    const topic = this.selectedTopic();
    const step = this.currentStudyStep();
    if (!topic || !step) return null;
    return this.progress.topic(topic.id).reviewPlans.find((plan) => plan.stepId === step.id) ?? null;
  }

  protected reviewDueLabel(plan: { dueAt: number }): string {
    const days = Math.ceil((plan.dueAt - Date.now()) / 86_400_000);
    return days <= 0 ? 'bugün' : days === 1 ? 'yarın' : `${days} gün sonra`;
  }

  protected reviewPlanLabel(plan: { ease: number; correctStreak: number }): string {
    if (plan.ease >= 1.2) return `Başarın nedeniyle aralık uzatıldı · ${plan.correctStreak} doğru tekrar`;
    if (plan.ease < 1) return 'Zorlandığın için daha kısa aralık önerildi';
    return 'Temel tekrar planı uygulanıyor';
  }

  protected startTopicQuiz(): void {
    const topic = this.selectedTopic();
    if (!topic || this.quizLoading()) return;
    this.quizLoading.set(true);
    this.quizError.set(null);
    this.quiz.set(null);
    this.quizAnswers.set({});
    this.quizSubmitted.set(false);
    this.api.generateTopicQuiz(topic.id).subscribe({
      next: (quiz) => {
        this.quiz.set(quiz);
        this.quizLoading.set(false);
      },
      error: (response) => {
        this.quizLoading.set(false);
        this.quizError.set(response.error?.detail ?? 'Kaynaklı mini quiz hazırlanamadı.');
      },
    });
  }

  protected selectQuizAnswer(questionId: string, optionId: string): void {
    if (this.quizSubmitted()) return;
    this.quizAnswers.update((answers) => ({ ...answers, [questionId]: optionId }));
  }

  protected canSubmitQuiz(): boolean {
    const quiz = this.quiz();
    return Boolean(quiz && quiz.questions.every((question) => this.quizAnswers()[question.id]));
  }

  protected submitQuiz(): void {
    const topic = this.selectedTopic();
    const quiz = this.quiz();
    if (!topic || !quiz || !this.canSubmitQuiz() || this.quizSubmitted()) return;
    this.quizSubmitted.set(true);
    const answers = this.quizAnswers();
    const incorrectSteps = quiz.questions
      .filter((question) => answers[question.id] !== question.correct_option_id)
      .map((question) => ({
        stepId: question.related_step_id,
        heading: this.studySteps().find((step) => step.id === question.related_step_id)?.heading ?? 'Tekrar gereken adım',
      }));
    const correctStepIds = quiz.questions
      .filter((question) => answers[question.id] === question.correct_option_id)
      .map((question) => question.related_step_id);
    const correct = quiz.questions.length - incorrectSteps.length;
    this.progress.recordQuiz(topic.id, correct, incorrectSteps, correctStepIds);
    this.showToast(incorrectSteps.length ? `${correct}/${quiz.questions.length} doğru. İlk tekrar yarın için planlandı.` : 'Harika! Tüm quiz sorularını doğru yanıtladın.', 'success');
  }

  protected quizAnswer(questionId: string): string | undefined {
    return this.quizAnswers()[questionId];
  }

  protected quizRelatedStep(stepId: string): TopicStudyStep | null {
    return this.studySteps().find((step) => step.id === stepId) ?? null;
  }

  protected isQuizQuestionReported(question: TopicQuizQuestion): boolean {
    const topic = this.selectedTopic();
    return Boolean(topic && this.feedbackKeys().includes(`quiz:${topic.id}:${question.id}`));
  }

  protected goToReviewStep(stepId: string): void {
    const index = this.studySteps().findIndex((step) => step.id === stepId);
    if (index < 0) return;
    this.studyMode.set('learn');
    this.studyStepIndex.set(index);
    const topic = this.selectedTopic();
    if (topic) this.progress.visit(topic.id, stepId);
    this.showToast('İlgili öğrenme adımına döndün.', 'success');
  }

  protected sendAnswerFeedback(helpful: boolean, response: ChatResponse): void {
    const topic = this.selectedTopic();
    const key = `answer:${response.conversation_id}:${helpful ? 'helpful' : 'unhelpful'}`;
    // Aynı yanıt için çelişkili iki oy üretmeyiz. Kullanıcı kararını daha sonra
    // Kalite Merkezi'nde inceleme durumunu değiştirerek yönetebilir.
    if (!topic || this.answerFeedbackState(response)) return;
    this.sendFeedback(
      key,
      helpful ? 'answer_helpful' : 'answer_unhelpful',
      { conversation_id: response.conversation_id, payload: { answer_preview: response.answer.slice(0, 500) } },
      helpful ? 'Geri bildirimin kaydedildi.' : 'Bu yanıt kalite incelemesi için işaretlendi.',
    );
  }

  protected answerFeedbackState(response: ChatResponse): 'helpful' | 'unhelpful' | null {
    const prefix = `answer:${response.conversation_id}:`;
    if (this.feedbackKeys().includes(`${prefix}helpful`) || this.feedbackKeys().includes(`${prefix}true`)) return 'helpful';
    if (this.feedbackKeys().includes(`${prefix}unhelpful`) || this.feedbackKeys().includes(`${prefix}false`)) return 'unhelpful';
    return null;
  }

  protected reportQuizQuestion(question: TopicQuizQuestion): void {
    const topic = this.selectedTopic();
    const key = `quiz:${topic?.id}:${question.id}`;
    if (!topic || this.feedbackKeys().includes(key)) return;
    this.sendFeedback(
      key,
      'quiz_question_issue',
      { payload: { question: question.question, related_step_id: question.related_step_id } },
      'Quiz sorusu kalite incelemesi için işaretlendi.',
    );
  }

  protected reportSourceMismatch(source: ChatSource, conversationId?: string): void {
    const topic = this.selectedTopic();
    if (!topic || !source.section_id) return;
    const key = `source:${source.section_id}`;
    if (this.feedbackKeys().includes(key)) return;
    this.sendFeedback(
      key,
      'source_mismatch',
      { conversation_id: conversationId, source_section_id: source.section_id, payload: { heading: source.heading_path, citation_index: source.index } },
      'Kaynak eşleşmesi kontrol edilmek üzere işaretlendi.',
      () => this.weakSourceSectionIds.update((ids) => [...new Set([...ids, source.section_id!])]),
    );
  }

  protected isWeakSource(source: ChatSource): boolean {
    return Boolean(source.section_id && this.weakSourceSectionIds().includes(source.section_id));
  }

  protected qualityApprovalLabel(feedback: QualityFeedback): string {
    return feedback.kind === 'answer_helpful' ? 'Olumlu sinyal olarak sakla' : 'Kalite sorunu olarak onayla';
  }

  protected qualityIgnoreLabel(feedback: QualityFeedback): string {
    return feedback.kind === 'answer_helpful' ? 'Başarı kaydını kapat' : 'Yanlış işaretleme — göz ardı et';
  }

  protected qualityReviewToast(feedback: QualityFeedback, reviewStatus: FeedbackReviewStatus): string {
    if (reviewStatus === 'ignored') return 'Kayıt kapatıldı ve kalite sorunlarından hariç tutuldu.';
    if (reviewStatus === 'new') return 'Kayıt yeniden inceleme kuyruğuna alındı.';
    return feedback.kind === 'answer_helpful' ? 'Olumlu başarı sinyali saklandı.' : 'Geri bildirim doğrulanmış kalite sorunu olarak kaydedildi.';
  }

  protected openSource(sectionId: string | null): void {
    if (!sectionId) return;
    this.sourceViewer.set(null);
    this.sourceViewerError.set(null);
    this.sourceViewerLoading.set(true);
    this.api.getSourceView(sectionId).subscribe({
      next: (source) => {
        this.sourceViewer.set(source);
        this.sourceViewerLoading.set(false);
      },
      error: (response) => {
        this.sourceViewerError.set(response.error?.detail ?? 'Kaynak bağlamı açılamadı.');
        this.sourceViewerLoading.set(false);
      },
    });
  }

  protected closeSourceViewer(): void {
    this.sourceViewer.set(null);
    this.sourceViewerError.set(null);
    this.sourceViewerLoading.set(false);
  }

  protected setQualityFilter(filter: FeedbackReviewStatus | 'all'): void {
    this.qualityFilter.set(filter);
    this.loadQualityCenter();
  }

  protected updateQualityReviewNote(feedbackId: string, note: string): void {
    this.qualityReviewNotes.update((notes) => ({ ...notes, [feedbackId]: note }));
  }

  protected reviewQualityFeedback(feedback: QualityFeedback, reviewStatus: FeedbackReviewStatus): void {
    if (this.qualityLoading()) return;
    this.qualityLoading.set(true);
    this.qualityError.set(null);
    const note = this.qualityReviewNotes()[feedback.id] ?? feedback.review_note ?? null;
    this.api.reviewQualityFeedback(feedback.id, reviewStatus, note?.trim() || null).subscribe({
      next: () => {
        this.showToast(this.qualityReviewToast(feedback, reviewStatus), 'success');
        this.loadQualityCenter();
      },
      error: (response) => {
        this.qualityLoading.set(false);
        this.qualityError.set(response.error?.detail ?? 'Geri bildirim güncellenemedi.');
      },
    });
  }

  protected qualityKindLabel(kind: FeedbackKind): string {
    return {
      answer_helpful: 'Olumlu geri bildirim',
      answer_unhelpful: 'Yararsız yanıt',
      quiz_question_issue: 'Quiz sorunu',
      source_mismatch: 'Kaynak eşleşmesi',
    }[kind];
  }

  protected qualityDate(value: string | null): string {
    if (!value) return 'Tarih yok';
    return new Intl.DateTimeFormat('tr-TR', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  }

  protected exportReviewedFeedback(): void {
    if (this.qualityExporting()) return;
    this.qualityExporting.set(true);
    this.api.exportReviewedFeedback().subscribe({
      next: (result) => {
        this.qualityExporting.set(false);
        window.open(`http://localhost:8000${result.download_url}`, '_blank', 'noopener');
        this.showToast(`${result.candidate_count} onaylı kalite adayı dışa aktarıldı.`, 'success');
      },
      error: (response) => { this.qualityExporting.set(false); this.showToast(response.error?.detail ?? 'Dışa aktarma tamamlanamadı.', 'error'); },
    });
  }

  private sendFeedback(
    key: string,
    kind: FeedbackKind,
    extra: { conversation_id?: string; source_section_id?: string; payload?: Record<string, unknown> },
    successMessage: string,
    afterSuccess?: () => void,
  ): void {
    const topic = this.selectedTopic();
    if (!topic) return;
    this.api.sendFeedback({
      client_id: this.progress.clientId,
      profile_id: this.progress.profileId ?? undefined,
      kind,
      topic_id: topic.id,
      ...extra,
    }).subscribe({
      next: () => {
        this.feedbackKeys.update((keys) => {
          const updated = [...new Set([...keys, key])];
          this.storeFeedbackKeys(updated);
          return updated;
        });
        afterSuccess?.();
        this.showToast(successMessage, 'success');
      },
      error: () => this.showToast('Geri bildirim kaydedilemedi.', 'error'),
    });
  }

  private loadStoredFeedbackKeys(): string[] {
    try {
      const value = JSON.parse(localStorage.getItem(this.feedbackStateStorageKey) ?? '[]');
      return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string').slice(-500) : [];
    } catch {
      return [];
    }
  }

  private storeFeedbackKeys(keys: string[]): void {
    try { localStorage.setItem(this.feedbackStateStorageKey, JSON.stringify(keys.slice(-500))); } catch { /* Tarayıcı depolaması kapalı olabilir. */ }
  }

  private loadPersistedFeedbackState(profileId: string): void {
    const scope = `${this.progress.clientId}:${profileId}`;
    if (this.feedbackStateScope === scope) return;
    this.feedbackStateScope = scope;
    this.api.getFeedbackState(this.progress.clientId, profileId).subscribe({
      next: (state) => this.feedbackKeys.update((keys) => {
        const updated = [...new Set([...keys, ...state.keys])].slice(-500);
        this.storeFeedbackKeys(updated);
        return updated;
      }),
      error: () => { this.feedbackStateScope = null; },
    });
  }

  protected askAboutCurrentStep(kind: 'explain' | 'check'): void {
    const topic = this.selectedTopic();
    const step = this.currentStudyStep();
    if (!topic || !step || this.stepAssistantLoading()) return;
    const prompt =
      kind === 'explain'
        ? studyStepExplainPrompt(topic.title, step.heading)
        : studyStepCheckPrompt(topic.title, step.heading);
    this.stepAssistantLoading.set(true);
    this.stepAssistantError.set(null);
    this.stepAssistantResponse.set(null);
    this.api.chat(prompt, topic.id, this.conversationId(), {
      pinnedSectionIds: step.section_ids,
      learningMode: kind === 'explain',
    }).subscribe({
      next: (response) => {
        this.conversationId.set(response.conversation_id);
        this.stepAssistantResponse.set(response);
        this.stepAssistantLoading.set(false);
      },
      error: (response) => {
        this.stepAssistantLoading.set(false);
        this.stepAssistantError.set(response.error?.detail ?? 'Bu adım için açıklama alınamadı.');
      },
    });
  }

  protected continueStepInChat(): void {
    const response = this.stepAssistantResponse();
    if (!response) return;
    this.studyMode.set('chat');
    this.chatResponse.set(response);
    this.chatDraft.set('');
    this.api.getConversation(response.conversation_id).subscribe({
      next: (conversation) => this.conversation.set(conversation),
    });
  }

  protected chooseUploadFiles(files: FileList | null, input: HTMLInputElement | null): void {
    if (this.uploadLoading()) return;
    this.stopAnalysisPolling();
    this.stopUploadQueuePolling();
    this.stopConversionReviewPolling();
    const selected = Array.from(files ?? []);
    this.uploadQueue.set(selected.map((file, index) => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${index}`,
      file,
      status: 'queued',
      progress: 0,
      message: this.isConvertibleFile(file) ? 'Dönüştürme sırasını bekliyor.' : 'Analiz sırasını bekliyor.',
      kind: this.isConvertibleFile(file) ? 'conversion' : 'analysis',
    })));
    this.uploadError.set(null);
    this.uploadSuccess.set(null);
    this.documentAnalysis.set(null);
    this.conversionReview.set(null);
    this.markdownEditing.set(false);
    this.markdownDraft.set('');
    this.stopConversionReviewPolling();
    this.approvedProposalIds.set([]);
    this.proposalDrafts.set({});
    this.lessonTitleDraft.set('');
    this.lessonMergePreview.set(null);
    if (input) input.value = '';
  }

  protected uploadDocument(): void {
    if (!this.uploadQueue().length || this.uploadLoading()) return;

    this.uploadLoading.set(true);
    this.uploadError.set(null);
    this.uploadSuccess.set(null);
    this.documentAnalysis.set(null);
    this.conversionReview.set(null);
    this.markdownEditing.set(false);
    this.markdownDraft.set('');
    this.processNextUpload();
  }

  protected clearUploadQueue(): void {
    if (this.uploadLoading()) return;
    this.uploadQueue.set([]);
    this.uploadSuccess.set(null);
    this.uploadError.set(null);
  }

  protected removeQueuedUpload(queueItemId: string): void {
    const item = this.uploadQueue().find((candidate) => candidate.id === queueItemId);
    if (!item || item.status !== 'queued') return;
    this.uploadQueue.update((items) => items.filter((candidate) => candidate.id !== queueItemId));
    this.toast.set({ message: `${item.file.name} kuyruktan çıkarıldı.`, tone: 'success' });
  }

  protected uploadQueueStatusLabel(status: UploadQueueStatus): string {
    return {
      queued: 'Bekliyor',
      analyzing: 'Analiz ediliyor',
      ready: 'Taslak hazır',
      failed: 'Tamamlanamadı',
    }[status];
  }

  private processNextUpload(): void {
    const next = this.uploadQueue().find((item) => item.status === 'queued');
    if (!next) {
      this.uploadLoading.set(false);
      const completed = this.uploadQueue().filter((item) => item.status === 'ready').length;
      const failed = this.uploadQueue().filter((item) => item.status === 'failed').length;
      this.uploadSuccess.set(
        failed ? `${completed} doküman incelemeye hazır; ${failed} doküman tamamlanamadı.` : `${completed} doküman incelemeye hazır.`
      );
      this.loadWorkspace();
      return;
    }

    this.updateUploadQueueItem(next.id, {
      status: 'analyzing', progress: 5, message: 'Doküman gönderiliyor.',
    });
    if (next.kind === 'conversion') {
      this.api.convertDocument(next.file).subscribe({
        next: (conversion) => {
          this.updateUploadQueueFromConversion(next.id, conversion);
          this.documentConversions.update((items) => ({ ...items, [conversion.document.id]: conversion }));
          if (conversion.status === 'processing') this.pollConversionQueueItem(next.id, conversion.document.id);
          else this.completeUploadQueueItem(next.id, conversion.status === 'failed');
        },
        error: (response) => {
          this.updateUploadQueueItem(next.id, {
            status: 'failed', progress: 100, message: response.error?.detail ?? 'Doküman dönüştürülemedi.',
          });
          this.processNextUpload();
        },
      });
      return;
    }
    this.api.analyzeDocument(next.file).subscribe({
      next: (analysis) => {
        this.updateUploadQueueFromAnalysis(next.id, analysis);
        this.documentDetails.update((items) => ({ ...items, [analysis.document.id]: analysis }));
        if (analysis.status === 'processing') this.pollUploadQueueItem(next.id, analysis.document.id);
        else this.completeUploadQueueItem(next.id, analysis.status === 'failed');
      },
      error: (response) => {
        this.updateUploadQueueItem(next.id, {
          status: 'failed', progress: 100, message: response.error?.detail ?? 'Doküman yüklenemedi.',
        });
        this.processNextUpload();
      },
    });
  }

  private pollUploadQueueItem(queueItemId: string, documentId: string): void {
    this.stopUploadQueuePolling();
    this.uploadQueuePollTimer = window.setTimeout(() => {
      this.api.getDocumentAnalysis(documentId).subscribe({
        next: (analysis) => {
          this.updateUploadQueueFromAnalysis(queueItemId, analysis);
          this.documentDetails.update((items) => ({ ...items, [analysis.document.id]: analysis }));
          if (analysis.status === 'processing') this.pollUploadQueueItem(queueItemId, documentId);
          else this.completeUploadQueueItem(queueItemId, analysis.status === 'failed');
        },
        error: () => {
          this.updateUploadQueueItem(queueItemId, {
            status: 'failed', progress: 100, message: 'Analiz durumu alınamadı.',
          });
          this.processNextUpload();
        },
      });
    }, 1_200);
  }

  private pollConversionQueueItem(queueItemId: string, documentId: string): void {
    this.stopUploadQueuePolling();
    this.uploadQueuePollTimer = window.setTimeout(() => {
      this.api.getDocumentConversion(documentId).subscribe({
        next: (conversion) => {
          this.updateUploadQueueFromConversion(queueItemId, conversion);
          this.documentConversions.update((items) => ({ ...items, [documentId]: conversion }));
          if (conversion.status === 'processing') this.pollConversionQueueItem(queueItemId, documentId);
          else this.completeUploadQueueItem(queueItemId, conversion.status === 'failed');
        },
        error: (response) => {
          this.updateUploadQueueItem(queueItemId, {
            status: 'failed', progress: 100, message: response.error?.detail ?? 'Dönüşüm durumu alınamadı.',
          });
          this.processNextUpload();
        },
      });
    }, 1_200);
  }

  private completeUploadQueueItem(queueItemId: string, failed: boolean): void {
    this.stopUploadQueuePolling();
    if (failed) this.updateUploadQueueItem(queueItemId, { status: 'failed', progress: 100 });
    this.loadWorkspace();
    this.processNextUpload();
  }

  private updateUploadQueueFromAnalysis(queueItemId: string, analysis: DocumentAnalysis): void {
    this.updateUploadQueueItem(queueItemId, {
      documentId: analysis.document.id,
      status: analysis.status === 'failed' ? 'failed' : analysis.status === 'ready' ? 'ready' : 'analyzing',
      progress: analysis.progress,
      message: analysis.error_message ?? analysis.message ?? 'İşlem sürüyor.',
    });
  }

  private updateUploadQueueFromConversion(queueItemId: string, conversion: DocumentConversion): void {
    this.updateUploadQueueItem(queueItemId, {
      documentId: conversion.document.id,
      status: conversion.status === 'failed' ? 'failed' : conversion.status === 'ready' ? 'ready' : 'analyzing',
      progress: conversion.progress,
      message: conversion.error_message ?? conversion.message ?? 'Dönüşüm sürüyor.',
    });
  }

  private isConvertibleFile(file: File): boolean {
    return ['.pdf', '.docx', '.pptx', '.xlsx'].some((suffix) => file.name.toLocaleLowerCase('tr-TR').endsWith(suffix));
  }

  private updateUploadQueueItem(queueItemId: string, changes: Partial<UploadQueueItem>): void {
    this.uploadQueue.update((items) => items.map((item) => item.id === queueItemId ? { ...item, ...changes } : item));
  }

  protected isProposalApproved(proposalId: string): boolean {
    return this.approvedProposalIds().includes(proposalId);
  }

  protected analysisStageLabel(stage: string): string {
    const labels: Record<string, string> = {
      queued: 'Analiz sırası',
      analyzing: 'Konu ve örnek analizi',
      saving: 'Taslak kaydediliyor',
      approval_queued: 'İndeksleme sırası',
      indexing: 'Konu eşleştirmeleri hazırlanıyor',
      vectorizing: 'Embedding ve Qdrant aktarımı',
      contextualizing: 'Yapay zekâ ile arama bağlamı hazırlanıyor',
      completed: 'Kaynak hazır',
      ready: 'İnceleme hazır',
      failed: 'Analiz tamamlanamadı',
    };
    return labels[stage] ?? 'Doküman hazırlanıyor';
  }

  protected workflowProgress(analysis: DocumentAnalysis): number {
    if (analysis.status === 'approved') return 100;
    if (analysis.status === 'ready') return 66;
    if (analysis.stage === 'approval_queued') return 68;
    if (analysis.stage === 'indexing') return 78;
    if (analysis.stage === 'contextualizing') return 78 + Math.round(Math.max(0, analysis.progress - 35) / 2);
    if (analysis.stage === 'vectorizing') return 90;
    return Math.min(55, Math.max(20, analysis.progress));
  }

  protected workflowStatusText(analysis: DocumentAnalysis): string {
    if (analysis.status === 'ready') return 'Konu taslağı hazır; incelemeniz ve onayınız bekleniyor.';
    if (analysis.status === 'approved') return analysis.message ?? 'Kaynak indekslendi ve öğrenmeye hazır.';
    if (['approval_queued', 'indexing', 'contextualizing', 'vectorizing'].includes(analysis.stage)) {
      return analysis.message ?? 'Onaylanan konular Qdrant’a aktarılıyor.';
    }
    return analysis.message ?? 'Konu analizi sürüyor.';
  }

  protected workflowStepState(analysis: DocumentAnalysis, step: 1 | 2 | 3): 'waiting' | 'active' | 'complete' {
    const indexing = ['approval_queued', 'indexing', 'contextualizing', 'vectorizing'].includes(analysis.stage);
    if (step === 1) {
      return analysis.status === 'processing' && !indexing ? 'active' : 'complete';
    }
    if (step === 2) {
      if (analysis.status === 'ready') return 'active';
      return indexing || analysis.status === 'approved' ? 'complete' : 'waiting';
    }
    if (analysis.status === 'approved') return 'complete';
    return indexing ? 'active' : 'waiting';
  }

  protected reanalyzeDocument(documentId?: string): void {
    const analysis = documentId ? this.documentDetail(documentId) : this.documentAnalysis();
    if (!analysis || this.reanalyzeLoading() || this.approvalLoading()) return;
    this.reanalyzeLoading.set(true);
    this.uploadError.set(null);
    this.api.reanalyzeDocument(analysis.document.id).subscribe({
      next: (nextAnalysis) => {
        this.reanalyzeLoading.set(false);
        this.approvedProposalIds.set([]);
        this.proposalDrafts.set({});
        this.documentDetails.update((items) => ({ ...items, [nextAnalysis.document.id]: nextAnalysis }));
        this.applyAnalysisState(nextAnalysis);
      },
      error: (response) => {
        this.reanalyzeLoading.set(false);
        this.uploadError.set(response.error?.detail ?? 'Konu analizi yeniden başlatılamadı.');
      },
    });
  }

  protected openAnalysisForReview(analysis: DocumentAnalysis): void {
    if (analysis.status !== 'ready') return;
    this.applyAnalysisState(analysis);
    window.setTimeout(() => document.getElementById('analysis-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  protected isDocumentExpanded(documentId: string): boolean {
    return this.expandedDocumentId() === documentId;
  }

  protected indexBusy(documentId: string): boolean {
    return this.indexStarting() === documentId || ['queued', 'running'].includes(this.indexJobs()[documentId]?.status ?? '');
  }

  protected indexDraft(document: SourceDocument): boolean {
    const job = this.indexJobs()[document.id];
    if (job && ['queued', 'running'].includes(job.status)) return job.contextual_enrichment;
    return this.indexDrafts()[document.id] ?? document.contextual_enrichment ?? false;
  }

  protected setIndexDraft(documentId: string, enabled: boolean): void {
    this.indexDrafts.update(items => ({ ...items, [documentId]: enabled }));
  }

  protected startReindex(document: SourceDocument): void {
    if (this.indexBusy(document.id)) return;
    this.indexStarting.set(document.id);
    this.api.startIndexJob(document.id, this.indexDraft(document)).subscribe({
      next: job => {
        this.indexStarting.set(null);
        this.indexJobs.update(items => ({ ...items, [document.id]: job }));
        this.refreshIndexJob(document.id);
      },
      error: response => {
        this.indexStarting.set(null);
        this.showToast(response.error?.detail ?? 'İndeks yenilemesi başlatılamadı.', 'error');
      },
    });
  }

  private refreshIndexJob(documentId: string): void {
    const timer = this.indexTimers.get(documentId);
    if (timer) window.clearTimeout(timer);
    this.indexTimers.delete(documentId);
    this.api.getIndexJob(documentId).subscribe({
      next: job => {
        const previous = this.indexJobs()[documentId];
        this.indexJobs.update(items => ({ ...items, [documentId]: job }));
        if (job && ['queued', 'running'].includes(job.status)) {
          this.indexTimers.set(documentId, window.setTimeout(() => this.refreshIndexJob(documentId), 1200));
        } else if (job?.status === 'completed') {
          this.documents.update(items => items.map(item => item.id === documentId ? { ...item, contextual_enrichment: job.contextual_enrichment } : item));
          if (previous && previous.status !== 'completed') this.showToast(job.message ?? 'İndeks yenilendi.', 'success');
        } else if (job?.status === 'failed' && previous && previous.status !== 'failed') {
          this.showToast(job.message ?? 'İndeks yenilenemedi.', 'error');
        }
      },
      error: () => this.showToast('İndeks durumu alınamadı. Sayfayı yenileyerek yeniden kontrol edebilirsiniz.', 'error'),
    });
  }

  protected documentWorkflowLabel(document: SourceDocument): string {
    if (document.status === 'active') return 'İndeksleme tamamlandı';
    const conversion = this.documentConversion(document.id);
    if (conversion) {
      if (conversion.status === 'ready') return 'Markdown incelemeye hazır';
      if (conversion.status === 'failed') return 'Dönüşüm tamamlanamadı';
      if (conversion.status === 'published') {
        const analysis = this.documentDetail(document.id);
        return analysis ? `Konu analizi · ${analysis.progress}%` : 'Konu analizi başlıyor';
      }
      return `Dönüştürülüyor · ${conversion.progress}%`;
    }
    const analysis = this.documentDetail(document.id);
    if (!analysis) return 'Durum yükleniyor';
    if (analysis.status === 'ready') return 'İnceleme hazır';
    if (analysis.status === 'failed') return 'İşlem tamamlanamadı';
    if (analysis.stage === 'approval_queued' || analysis.stage === 'indexing' || analysis.stage === 'vectorizing') {
      return `İndeksleniyor · ${analysis.progress}%`;
    }
    return `Analiz ediliyor · ${analysis.progress}%`;
  }

  protected documentWorkflowState(document: SourceDocument): 'ready' | 'active' | 'failed' | 'processing' {
    if (document.status === 'active') return 'active';
    const conversion = this.documentConversion(document.id);
    if (conversion?.status === 'ready') return 'ready';
    if (conversion?.status === 'failed') return 'failed';
    const analysis = this.documentDetail(document.id);
    if (analysis?.status === 'ready') return 'ready';
    if (analysis?.status === 'failed') return 'failed';
    return 'processing';
  }

  protected documentDetail(documentId: string): DocumentAnalysis | null {
    return this.documentDetails()[documentId] ?? null;
  }

  protected documentConversion(documentId: string): DocumentConversion | null {
    return this.documentConversions()[documentId] ?? null;
  }

  protected toggleDocumentDetails(document: SourceDocument): void {
    if (this.isDocumentExpanded(document.id)) {
      this.expandedDocumentId.set(null);
      return;
    }
    this.expandedDocumentId.set(document.id);
    this.loadDocumentDetails(document);
  }

  protected isLessonExpanded(lessonId: string): boolean {
    return this.expandedLessonId() === lessonId;
  }

  protected toggleLesson(group: LessonGroup): void {
    if (this.isLessonExpanded(group.id)) {
      this.expandedLessonId.set(null);
      return;
    }
    this.expandedLessonId.set(group.id);
    for (const document of group.documents) this.loadDocumentDetails(document);
  }

  protected lessonGroupTopics(group: LessonGroup): { title: string; description: string | null }[] {
    const topics = new Map<string, { title: string; description: string | null }>();
    for (const document of group.documents) {
      for (const proposal of this.documentDetail(document.id)?.topics ?? []) {
        const key = proposal.title.toLocaleLowerCase('tr-TR');
        if (!topics.has(key)) topics.set(key, { title: proposal.title, description: proposal.description });
      }
    }
    return [...topics.values()];
  }

  protected isLessonGroupLoading(group: LessonGroup): boolean {
    return group.documents.some((document) => this.documentDetailLoading() === document.id);
  }

  protected selectTopicFromProposal(title: string): void {
    const normalizedTitle = title.toLocaleLowerCase('tr-TR');
    const topic = this.topics().find((item) => item.title.toLocaleLowerCase('tr-TR') === normalizedTitle);
    if (topic) this.selectTopic(topic);
  }

  protected topicProgressLabel(title: string): string {
    const topic = this.topics().find((item) => item.title.toLocaleLowerCase('tr-TR') === title.toLocaleLowerCase('tr-TR'));
    if (!topic) return 'Henüz başlanmadı';
    const progress = this.progress.topic(topic.id);
    if (!progress.lastVisitedAt) return 'Henüz başlanmadı';
    const score = progress.lastQuizScore === null ? '' : ` · Son quiz: ${progress.lastQuizScore}/3`;
    return `${progress.completedStepIds.length} adım tamamlandı${score}`;
  }

  private loadDocumentDetails(document: SourceDocument): void {
    const existingConversion = this.documentConversion(document.id);
    if (this.documentDetail(document.id) || (existingConversion && existingConversion.status !== 'published')) return;
    this.documentDetailLoading.set(document.id);
    this.api.getDocumentConversion(document.id).subscribe({
      next: (conversion) => {
        this.documentDetailLoading.set(null);
        this.documentConversions.update((items) => ({ ...items, [document.id]: conversion }));
        if (conversion.status === 'published') {
          this.api.getDocumentAnalysis(document.id).subscribe({
            next: (analysis) => this.documentDetails.update((items) => ({ ...items, [document.id]: analysis })),
          });
        }
      },
      error: (response) => {
        if (response.status !== 404) {
          this.documentDetailLoading.set(null);
          this.uploadError.set(response.error?.detail ?? 'Doküman ayrıntısı alınamadı.');
          return;
        }
        this.api.getDocumentAnalysis(document.id).subscribe({
          next: (analysis) => {
            this.documentDetailLoading.set(null);
            this.documentDetails.update((items) => ({ ...items, [document.id]: analysis }));
          },
          error: (analysisResponse) => {
            this.documentDetailLoading.set(null);
            this.uploadError.set(analysisResponse.error?.detail ?? 'Doküman ayrıntısı alınamadı.');
          },
        });
      },
    });
  }

  protected openConversionForReview(conversion: DocumentConversion): void {
    if (conversion.status !== 'ready') return;
    this.conversionReview.set(conversion);
    this.selectedAssetIds.set([]);
    this.selectedConversionAssetId.set(conversion.assets.find((asset) => asset.table_repair_required && asset.inclusion_status === 'pending')?.id ?? conversion.assets[0]?.id ?? null);
    this.markdownEditing.set(false);
    this.markdownDraft.set(conversion.markdown ?? '');
    this.documentAnalysis.set(null);
    window.setTimeout(() => document.getElementById('conversion-review')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  protected documentAssetUrl(asset: DocumentAsset): string {
    return this.api.documentAssetUrl(asset);
  }

  protected downloadConversionAsset(conversion: DocumentConversion, asset: DocumentAsset): void {
    if (this.assetDownloadId()) return;
    this.assetDownloadId.set(asset.id);
    this.api.downloadDocumentAsset(conversion.document.id, asset.id).subscribe({
      next: (blob) => {
        const filename = this.conversionAssetFilename(conversion, asset);
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        link.hidden = true;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
        this.assetDownloadId.set(null);
        this.showToast(`${filename} indirme işlemi başlatıldı.`, 'success');
      },
      error: (response) => {
        this.assetDownloadId.set(null);
        this.showToast(response.status ? `Seçili görsel indirilemedi (HTTP ${response.status}).` : 'Seçili görsel indirilemedi; API bağlantısı veya CORS ayarlarını kontrol edin.', 'error');
      },
    });
  }

  protected selectConversionAsset(assetId: string): void {
    this.selectedConversionAssetId.set(assetId);
  }

  protected conversionAssetOptionLabel(asset: DocumentAsset, index: number): string {
    const kind = asset.kind === 'table' ? 'Tablo' : 'Görsel';
    const page = asset.page_number ? ` · ${asset.page_number}. sayfa` : '';
    const status = asset.analysis_status === 'ready'
      ? asset.inclusion_status === 'accepted'
        ? 'Markdown’da'
        : asset.inclusion_status === 'rejected'
          ? 'Hariç tutuldu'
          : asset.table_repair_required ? 'Tablo onayı bekliyor' : 'Açıklama hazır'
      : asset.analysis_status === 'processing'
        ? 'İşleniyor'
        : asset.analysis_status === 'failed'
          ? 'Hata'
          : 'Bekliyor';
    return `${index + 1}. ${kind}${page} · ${status}${asset.table_repair_required ? ' · Yeniden çıkarılan tablo' : ''}`;
  }

  protected pendingRepairTables(conversion: DocumentConversion): DocumentAsset[] {
    return conversion.assets.filter((asset) => asset.table_repair_required && asset.inclusion_status === 'pending');
  }

  private conversionAssetFilename(conversion: DocumentConversion, asset: DocumentAsset): string {
    const sourceName = conversion.document.source_name || 'ders-dokumani';
    const baseName = sourceName.replace(/\.[^.]+$/, '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').trim() || 'ders-dokumani';
    const index = conversion.assets.findIndex((item) => item.id === asset.id) + 1;
    const kind = asset.kind === 'table' ? 'tablo' : 'gorsel';
    const page = asset.page_number ? `-sayfa-${asset.page_number}` : '';
    return `${baseName}${page}-${kind}-${String(index).padStart(2, '0')}.png`;
  }

  protected hasVisualAnalysisInProgress(conversion: DocumentConversion): boolean {
    return conversion.assets.some((asset) => asset.analysis_status === 'processing');
  }

  protected startMarkdownEditing(): void {
    const conversion = this.conversionReview();
    if (!conversion || this.hasVisualAnalysisInProgress(conversion)) return;
    this.markdownDraft.set(conversion.markdown ?? '');
    this.markdownEditing.set(true);
    this.uploadError.set(null);
  }

  protected cancelMarkdownEditing(): void {
    this.markdownEditing.set(false);
    this.markdownDraft.set(this.conversionReview()?.markdown ?? '');
  }

  protected updateMarkdownDraft(value: string): void {
    this.markdownDraft.set(value);
  }

  protected saveMarkdownEditing(): void {
    const conversion = this.conversionReview();
    if (!conversion?.markdown_hash || this.markdownSaving()) return;
    this.markdownSaving.set(true);
    this.uploadError.set(null);
    this.api.saveDocumentConversionMarkdown(conversion.document.id, this.markdownDraft(), conversion.markdown_hash).subscribe({
      next: (updated) => {
        this.markdownSaving.set(false);
        this.markdownEditing.set(false);
        this.markdownDraft.set(updated.markdown ?? '');
        this.conversionReview.set(updated);
        this.documentConversions.update((items) => ({ ...items, [updated.document.id]: updated }));
        this.toast.set({ message: `Markdown sürüm ${updated.current_revision_number} olarak kaydedildi.`, tone: 'success' });
      },
      error: (response) => {
        this.markdownSaving.set(false);
        this.uploadError.set(response.error?.detail ?? 'Markdown değişiklikleri kaydedilemedi.');
      },
    });
  }

  protected downloadConversionMarkdown(conversion: DocumentConversion): void {
    const markdown = conversion.markdown;
    if (!markdown || this.markdownEditing() || this.hasVisualAnalysisInProgress(conversion)) return;
    const sourceName = conversion.document.source_name || 'ders-dokumani';
    const baseName = sourceName
      .replace(/\.[^.]+$/, '')
      .replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_')
      .trim() || 'ders-dokumani';
    const filename = `${baseName}.md`;
    const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    link.hidden = true;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1_000);
    this.showToast(`${filename} indirme işlemi başlatıldı.`, 'success');
  }

  protected downloadConversionBundle(conversion: DocumentConversion): void {
    this.api.downloadConversionBundle(conversion.document.id).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = 'konulab-markdown.zip';
        link.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 1000);
        this.showToast('Markdown ve görsel paketi indiriliyor.', 'success');
      },
      error: () => this.showToast('Paket indirilemedi. API sürümünü ve dosyaların durumunu kontrol edin.', 'error'),
    });
  }

  protected restoreMarkdownRevision(revisionNumber: number): void {
    const conversion = this.conversionReview();
    if (!conversion?.markdown_hash || this.markdownRestoring() !== null) return;
    if (!window.confirm(`Sürüm ${revisionNumber} içeriği yeni bir sürüm olarak geri yüklensin mi?`)) return;
    this.markdownRestoring.set(revisionNumber);
    this.uploadError.set(null);
    this.api.restoreDocumentConversionMarkdown(conversion.document.id, revisionNumber, conversion.markdown_hash).subscribe({
      next: (updated) => {
        this.markdownRestoring.set(null);
        this.markdownEditing.set(false);
        this.markdownDraft.set(updated.markdown ?? '');
        this.conversionReview.set(updated);
        this.documentConversions.update((items) => ({ ...items, [updated.document.id]: updated }));
        this.toast.set({ message: `Sürüm ${revisionNumber}, yeni sürüm ${updated.current_revision_number} olarak geri yüklendi.`, tone: 'success' });
      },
      error: (response) => {
        this.markdownRestoring.set(null);
        this.uploadError.set(response.error?.detail ?? 'Markdown sürümü geri yüklenemedi.');
      },
    });
  }

  protected analyzeConversionAsset(asset: DocumentAsset, refineOnly = false): void {
    const conversion = this.conversionReview();
    if (!conversion || asset.analysis_status === 'processing' || this.visualAnalysisLoading() || this.markdownEditing()) return;
    this.visualAnalysisLoading.set(true);
    this.uploadError.set(null);
    this.api.analyzeDocumentAsset(conversion.document.id, asset.id, refineOnly).subscribe({
      next: (updatedAsset) => {
        this.visualAnalysisLoading.set(false);
        this.applyConversionAssetUpdate(conversion.document.id, updatedAsset);
        this.pollConversionReview(conversion.document.id);
      },
      error: (response) => {
        this.visualAnalysisLoading.set(false);
        this.uploadError.set(response.error?.detail ?? 'Görsel açıklaması başlatılamadı.');
      },
    });
  }

  protected analyzeAllConversionAssets(): void {
    const conversion = this.conversionReview();
    if (!conversion || this.visualAnalysisLoading() || this.markdownEditing()) return;
    this.visualAnalysisLoading.set(true);
    this.api.analyzeAllDocumentAssets(conversion.document.id).subscribe({
      next: () => { this.visualAnalysisLoading.set(false); this.pollConversionReview(conversion.document.id); },
      error: (response) => { this.visualAnalysisLoading.set(false); this.uploadError.set(response.error?.detail ?? 'Toplu görsel açıklaması başlatılamadı.'); },
    });
  }

  protected toggleConversionAsset(assetId: string, selected: boolean): void {
    this.selectedAssetIds.update((ids) => selected ? [...new Set([...ids, assetId])] : ids.filter((id) => id !== assetId));
  }

  protected isConversionAssetSelected(assetId: string): boolean { return this.selectedAssetIds().includes(assetId); }

  protected analyzeSelectedConversionAssets(): void {
    const conversion = this.conversionReview();
    const selected = this.selectedAssetIds();
    if (!conversion || !selected.length || this.visualAnalysisLoading() || this.markdownEditing()) return;
    this.visualAnalysisLoading.set(true);
    this.api.analyzeAllDocumentAssets(conversion.document.id, selected).subscribe({ next: () => { this.visualAnalysisLoading.set(false); this.pollConversionReview(conversion.document.id); }, error: (response) => { this.visualAnalysisLoading.set(false); this.uploadError.set(response.error?.detail ?? 'Seçilen görseller açıklanamadı.'); } });
  }

  protected visualProgress(conversion: DocumentConversion): string {
    const total = conversion.assets.length;
    const ready = conversion.assets.filter((asset) => asset.analysis_status === 'ready').length;
    const processing = conversion.assets.filter((asset) => asset.analysis_status === 'processing').length;
    return `${ready} / ${total} hazır${processing ? ` · ${processing} işleniyor` : ''}`;
  }

  protected decideConversionAsset(asset: DocumentAsset, decision: 'accepted' | 'rejected'): void {
    const conversion = this.conversionReview();
    if (!conversion || (asset.analysis_status !== 'ready' && !(asset.table_repair_required && decision === 'rejected' && asset.analysis_status !== 'processing'))) return;
    this.api.decideDocumentAssetInclusion(conversion.document.id, asset.id, decision).subscribe({
      next: (updated) => { this.applyConversionAssetUpdate(conversion.document.id, updated); this.api.getDocumentConversion(conversion.document.id).subscribe({ next: (fresh) => { this.conversionReview.set(fresh); this.documentConversions.update((items) => ({ ...items, [fresh.document.id]: fresh })); } }); },
      error: (response) => this.uploadError.set(response.error?.detail ?? 'Görsel kararı kaydedilemedi.'),
    });
  }

  protected acceptAllConversionAssets(): void {
    const conversion = this.conversionReview();
    if (!conversion || this.hasVisualAnalysisInProgress(conversion)) return;
    this.api.acceptAllDocumentAssets(conversion.document.id).subscribe({ next: (updated) => { this.conversionReview.set(updated); this.documentConversions.update((items) => ({ ...items, [updated.document.id]: updated })); }, error: (response) => this.uploadError.set(response.error?.detail ?? 'Toplu kabul kaydedilemedi.') });
  }

  private applyConversionAssetUpdate(documentId: string, asset: DocumentAsset): void {
    const update = (conversion: DocumentConversion | null): DocumentConversion | null => {
      if (!conversion || conversion.document.id !== documentId) return conversion;
      return { ...conversion, assets: conversion.assets.map((item) => item.id === asset.id ? asset : item) };
    };
    this.conversionReview.update(update);
    this.documentConversions.update((items) => {
      const conversion = update(items[documentId] ?? null);
      return conversion ? { ...items, [documentId]: conversion } : items;
    });
  }

  private pollConversionReview(documentId: string): void {
    this.stopConversionReviewPolling();
    this.conversionReviewPollTimer = window.setTimeout(() => {
      this.api.getDocumentConversion(documentId).subscribe({
        next: (conversion) => {
          this.documentConversions.update((items) => ({ ...items, [documentId]: conversion }));
          if (this.conversionReview()?.document.id === documentId) this.conversionReview.set(conversion);
          if (conversion.assets.some((asset) => asset.analysis_status === 'processing')) this.pollConversionReview(documentId);
        },
        error: () => this.uploadError.set('Görsel açıklama durumu alınamadı. Sayfayı yenileyip tekrar deneyin.'),
      });
    }, 1_200);
  }

  protected publishConversion(): void {
    const conversion = this.conversionReview();
    if (!conversion || conversion.status !== 'ready' || this.approvalLoading()) return;
    if (this.hasVisualAnalysisInProgress(conversion)) {
      this.uploadError.set('Görsel açıklama tamamlanmadan Markdown yayınlanamaz.');
      return;
    }
    this.approvalLoading.set(true);
    this.stopConversionReviewPolling();
    this.uploadError.set(null);
    this.api.publishConversion(conversion.document.id, this.formulaWarningsAccepted(conversion), conversion.markdown_hash).subscribe({
      next: (analysis) => {
        this.approvalLoading.set(false);
        this.conversionReview.set(null);
        this.documentConversions.update((items) => ({
          ...items,
          [conversion.document.id]: { ...conversion, status: 'published', stage: 'topic_analysis', message: 'Konu önerileri hazırlanıyor.' },
        }));
        this.applyAnalysisState(analysis);
        this.loadWorkspace();
      },
      error: (response) => {
        this.approvalLoading.set(false);
        this.uploadError.set(response.error?.detail ?? 'Markdown öğrenme haritasına gönderilemedi.');
      },
    });
  }

  protected formulaWarningsAccepted(conversion: DocumentConversion): boolean {
    return !!conversion.markdown_hash && this.formulaAcceptanceKey() === `${conversion.document.id}:${conversion.markdown_hash}`;
  }

  protected acceptFormulaWarnings(conversion: DocumentConversion, accepted: boolean): void {
    this.formulaAcceptanceKey.set(accepted ? `${conversion.document.id}:${conversion.markdown_hash}` : null);
  }

  protected formulaSourceUrl(conversion: DocumentConversion, index: number): string {
    return this.api.formulaImageUrl(conversion.document.id, index);
  }

  protected isDocumentSelected(documentId: string): boolean {
    return this.selectedDocumentIds().includes(documentId);
  }

  protected toggleDocumentSelection(documentId: string, selected: boolean): void {
    this.selectedDocumentIds.update((ids) => selected ? [...new Set([...ids, documentId])] : ids.filter((id) => id !== documentId));
  }

  protected allDocumentsSelected(): boolean {
    return this.documents().length > 0 && this.documents().every((document) => this.isDocumentSelected(document.id));
  }

  protected toggleAllDocuments(selected: boolean): void {
    this.selectedDocumentIds.set(selected ? this.documents().map((document) => document.id) : []);
  }

  protected requestDocumentDeletion(document: SourceDocument): void {
    this.requestDocumentsDeletion([document]);
  }

  protected requestSelectedDocumentsDeletion(): void {
    const selectedIds = new Set(this.selectedDocumentIds());
    const selectedDocuments = this.documents().filter((document) => selectedIds.has(document.id));
    if (selectedDocuments.length) this.requestDocumentsDeletion(selectedDocuments);
  }

  protected cancelDocumentDeletion(): void {
    this.documentsToDelete.set([]);
    this.deletionImpact.set(null);
    this.deletionImpactLoading.set(false);
    this.deletionImpactError.set(null);
  }

  protected deletionImpactTopicTitles(impact: DocumentDeletionImpact): string {
    const visible = impact.affected_topics.slice(0, 5).map((topic) => topic.title);
    const remaining = impact.affected_topics.length - visible.length;
    return `${visible.join(', ')}${remaining > 0 ? ` ve ${remaining} konu daha` : ''}`;
  }

  private requestDocumentsDeletion(documents: SourceDocument[]): void {
    this.documentsToDelete.set(documents);
    this.deletionImpact.set(null);
    this.deletionImpactError.set(null);
    this.deletionImpactLoading.set(true);
    const requestedIds = documents.map((document) => document.id).sort().join(',');
    this.api.getDocumentDeletionImpact(documents.map((document) => document.id)).subscribe({
      next: (impact) => {
        if (this.documentsToDelete().map((document) => document.id).sort().join(',') !== requestedIds) return;
        this.deletionImpactLoading.set(false);
        this.deletionImpact.set(impact);
      },
      error: (response) => {
        if (this.documentsToDelete().map((document) => document.id).sort().join(',') !== requestedIds) return;
        this.deletionImpactLoading.set(false);
        this.deletionImpactError.set(response.error?.detail ?? 'Silme etkisi hesaplanamadı.');
      },
    });
  }

  protected deleteDocuments(): void {
    const documents = this.documentsToDelete();
    if (!documents.length || this.deletingDocumentId() || this.deletionImpactLoading() || this.deletionImpactError()) return;
    this.deletingDocumentId.set(documents[0].id);
    this.api.deleteDocuments(documents.map((document) => document.id)).subscribe({
      next: (response) => {
        this.deletingDocumentId.set(null);
        this.documentsToDelete.set([]);
        this.deletionImpact.set(null);
        this.expandedDocumentId.set(null);
        this.expandedLessonId.set(null);
        this.documentDetails.update((items) => {
          const remaining = { ...items };
          for (const document of documents) delete remaining[document.id];
          return remaining;
        });
        if (this.documentAnalysis() && documents.some((document) => document.id === this.documentAnalysis()?.document.id)) {
          this.stopAnalysisPolling();
          this.documentAnalysis.set(null);
        }
        this.selectedDocumentIds.set([]);
        this.showToast(`${response.deleted_count} doküman ve buna bağlı indeks kayıtları kaldırıldı.`, 'success');
        this.loadWorkspace();
      },
      error: (response) => {
        this.deletingDocumentId.set(null);
        this.showToast(response.error?.detail ?? 'Doküman kaldırılamadı.', 'error');
      },
    });
  }

  protected toggleProposal(proposalId: string, checked: boolean): void {
    this.approvedProposalIds.update((ids) => checked ? [...new Set([...ids, proposalId])] : ids.filter((id) => id !== proposalId));
  }

  protected proposalDraft(proposalId: string): { title: string; description: string } {
    return this.proposalDrafts()[proposalId] ?? { title: '', description: '' };
  }

  protected updateProposalDraft(proposalId: string, field: 'title' | 'description', value: string): void {
    this.proposalDrafts.update((drafts) => ({
      ...drafts,
      [proposalId]: { ...this.proposalDraft(proposalId), [field]: value },
    }));
  }

  protected updateLessonTitleDraft(value: string): void {
    this.lessonTitleDraft.set(value);
  }

  protected approveAnalysis(allowExistingLessonMerge = false): void {
    const analysis = this.documentAnalysis();
    const proposalIds = this.approvedProposalIds();
    if (!analysis || !proposalIds.length || this.approvalLoading() || this.lessonMergeChecking()) return;
    const lessonTitle = this.lessonTitleDraft().trim();
    if (lessonTitle.length < 2) {
      this.uploadError.set('Ders adı en az iki karakter olmalıdır.');
      return;
    }
    const decisions: TopicApprovalDecision[] = proposalIds.map((proposalId) => ({
      proposal_id: proposalId,
      title: this.proposalDraft(proposalId).title.trim(),
      description: this.proposalDraft(proposalId).description.trim() || null,
    }));
    if (decisions.some((decision) => decision.title.length < 2)) {
      this.uploadError.set('Her onaylı konu için en az iki karakterlik bir ad girin.');
      return;
    }

    if (!allowExistingLessonMerge) {
      this.lessonMergeChecking.set(true);
      this.uploadError.set(null);
      this.api.previewLessonMerge(analysis.document.id, lessonTitle, decisions).subscribe({
        next: (preview) => {
          this.lessonMergeChecking.set(false);
          if (preview.matching_documents.length) {
            this.lessonMergePreview.set(preview);
            return;
          }
          this.submitAnalysisApproval(lessonTitle, decisions, false);
        },
        error: (response) => {
          this.lessonMergeChecking.set(false);
          this.uploadError.set(response.error?.detail ?? 'Ders birleştirme durumu kontrol edilemedi.');
        },
      });
      return;
    }
    // Kullanıcı birleşmeyi onayladıktan sonra arka plan indekslemesini bekletmeden
    // pencereyi kapat; tek görünür durum üstteki işleme kartı olsun.
    this.lessonMergePreview.set(null);
    this.submitAnalysisApproval(lessonTitle, decisions, true);
  }

  protected cancelLessonMerge(): void {
    this.lessonMergePreview.set(null);
  }

  protected closeAnalysisCard(): void {
    this.stopAnalysisPolling();
    this.documentAnalysis.set(null);
    this.indexingDocumentId.set(null);
  }

  private submitAnalysisApproval(lessonTitle: string, decisions: TopicApprovalDecision[], allowExistingLessonMerge: boolean): void {
    const analysis = this.documentAnalysis();
    if (!analysis) return;
    this.approvalLoading.set(true);
    this.uploadError.set(null);
    this.api.approveDocumentAnalysis(analysis.document.id, lessonTitle, decisions, allowExistingLessonMerge, this.contextualEnrichment()).subscribe({
      next: (nextAnalysis) => {
        this.approvalLoading.set(false);
        this.lessonMergePreview.set(null);
        this.indexingDocumentId.set(nextAnalysis.document.id);
        this.applyAnalysisState(nextAnalysis);
      },
      error: (response) => {
        this.approvalLoading.set(false);
        this.uploadError.set(response.error?.detail ?? 'Konu onayı uygulanamadı.');
      },
    });
  }

  protected askAboutTopic(): void {
    const message = this.chatDraft().trim();
    if (!message || this.chatLoading()) return;

    this.chatLoading.set(true);
    this.chatError.set(null);
    this.chatResponse.set(null);
    this.api.chat(message, this.selectedTopic()?.id, this.conversationId()).subscribe({
      next: (response) => {
        this.conversationId.set(response.conversation_id);
        this.chatResponse.set(response);
        this.api.getConversation(response.conversation_id).subscribe({
          next: (conversation) => this.conversation.set(conversation),
        });
        this.chatLoading.set(false);
      },
      error: (response) => {
        this.chatLoading.set(false);
        this.chatError.set(response.error?.detail ?? 'Sohbet yanıtı alınamadı. Ollama ve kaynak indeksini kontrol edin.');
      },
    });
  }

  protected childTopicsFor(topic: Topic): Topic[] {
    return this.childTopics().get(topic.id) ?? [];
  }

  protected providerDiscovery(provider: LlmProvider): ProviderDiscovery | null {
    return this.runtimeModelSettings()?.providers.find((item) => item.provider === provider) ?? null;
  }

  protected providerModels(provider: LlmProvider) {
    return this.providerDiscovery(provider)?.models ?? [];
  }

  protected providerLabel(provider: LlmProvider): string { return provider === 'ollama' ? 'Ollama' : 'vLLM'; }

  protected updateRuntimeModelDraft(field: 'text_provider' | 'ollama_base_url' | 'ollama_model' | 'vllm_base_url' | 'vllm_model' | 'vision_provider' | 'vision_model' | 'context_length', value: string): void {
    this.runtimeModelDraft.update((draft) => {
      if (!draft) return draft;
      const parsed = field === 'context_length' ? Number(value) : value;
      return { ...draft, [field]: parsed } as RuntimeModelSettings;
    });
    this.runtimeModelError.set(null);
    this.runtimeModelTestMessage.set(null);
  }

  protected testRuntimeModelSettings(): void {
    const draft = this.runtimeModelDraft();
    if (!draft || this.runtimeModelTesting()) return;
    this.runtimeModelTesting.set(true);
    this.runtimeModelError.set(null);
    this.runtimeModelTestMessage.set(null);
    this.api.testRuntimeModelSettings(this.runtimeModelPayload(draft)).subscribe({
      next: (result) => { this.runtimeModelTesting.set(false); this.runtimeModelTestMessage.set(result.message); },
      error: (response) => { this.runtimeModelTesting.set(false); this.runtimeModelError.set(response.error?.detail ?? 'Sağlayıcı ve model denemesi başarısız oldu.'); },
    });
  }

  protected saveRuntimeModelSettings(): void {
    const draft = this.runtimeModelDraft();
    if (!draft || this.runtimeModelSaving()) return;
    this.runtimeModelSaving.set(true);
    this.runtimeModelError.set(null);
    this.api.updateRuntimeModelSettings(this.runtimeModelPayload(draft)).subscribe({
      next: (settings) => {
        this.runtimeModelSaving.set(false);
        this.runtimeModelSettings.set(settings);
        this.runtimeModelDraft.set({ ...settings });
        this.runtimeModelTestMessage.set(`Ayarlar uygulandı. Etkin metin modeli: ${settings.text_provider === 'ollama' ? settings.ollama_model : settings.vllm_model}.`);
      },
      error: (response) => { this.runtimeModelSaving.set(false); this.runtimeModelError.set(response.error?.detail ?? 'Ayarlar kaydedilemedi; önceki çalışan ayar korundu.'); },
    });
  }

  protected reloadRuntimeModelSettings(): void { this.loadRuntimeModelSettings(); }

  private runtimeModelPayload(settings: RuntimeModelSettings) {
    return {
      text_provider: settings.text_provider,
      ollama_base_url: settings.ollama_base_url,
      ollama_model: settings.ollama_model,
      vllm_base_url: settings.vllm_base_url,
      vllm_model: settings.vllm_model,
      vision_provider: settings.vision_provider,
      vision_model: settings.vision_model,
      context_length: settings.context_length,
    };
  }

  private loadWorkspace(): void {
    this.api.getTopics().subscribe({
      next: (topics) => {
        this.topics.set(topics);
        const initialTopic = topics.find((topic) => topic.slug === 'tokenization') ?? topics[0] ?? null;
        if (initialTopic) this.selectTopic(initialTopic);
        else this.selectedTopic.set(null);
      },
      error: () => this.error.set('API bağlantısı kurulamadı. FastAPI sunucusunu başlatın.')
    });
    this.api.getDocuments().subscribe({
      next: (documents) => {
        this.documents.set(documents);
        for (const document of documents) {
          if (document.status === 'active') this.refreshIndexJob(document.id);
          if (document.status === 'active' || this.documentDetail(document.id) || this.documentConversion(document.id)) continue;
          this.api.getDocumentConversion(document.id).subscribe({
            next: (conversion) => {
              this.documentConversions.update((items) => ({ ...items, [document.id]: conversion }));
              if (conversion.status === 'published') {
                this.api.getDocumentAnalysis(document.id).subscribe({
                  next: (analysis) => this.documentDetails.update((items) => ({ ...items, [document.id]: analysis })),
                });
              }
            },
            error: (response) => {
              if (response.status !== 404) return;
              this.api.getDocumentAnalysis(document.id).subscribe({
                next: (analysis) => this.documentDetails.update((items) => ({ ...items, [document.id]: analysis })),
              });
            },
          });
        }
      },
      error: () => this.error.set('API bağlantısı kurulamadı. FastAPI sunucusunu başlatın.')
    });
  }

  private loadDashboard(): void {
    const profile = this.learnerProfile();
    if (!profile || this.dashboardLoading()) return;
    this.dashboardLoading.set(true);
    this.dashboardError.set(null);
    this.api.getLearnerDashboard(profile.id, this.progress.clientId).subscribe({
      next: (dashboard) => {
        this.dashboard.set(dashboard);
        this.dashboardLoading.set(false);
      },
      error: (response) => {
        this.dashboardError.set(response.error?.detail ?? 'Öğrenme özeti yüklenemedi.');
        this.dashboardLoading.set(false);
      },
    });
  }

  private loadQualityCenter(): void {
    this.qualityLoading.set(true);
    this.qualityError.set(null);
    this.api.getQualityOverview().subscribe({
      next: (overview) => this.qualityOverview.set(overview),
      error: (response) => this.qualityError.set(response.error?.detail ?? 'Kalite özeti yüklenemedi.'),
    });
    this.api.getQualityInsights().subscribe({ next: (insights) => this.qualityInsights.set(insights) });
    this.api.getQualityFeedback(this.qualityFilter()).subscribe({
      next: (feedback) => {
        this.qualityFeedback.set(feedback);
        this.qualityReviewNotes.set(Object.fromEntries(feedback.map((item) => [item.id, item.review_note ?? ''])));
        this.qualityLoading.set(false);
      },
      error: (response) => {
        this.qualityError.set(response.error?.detail ?? 'Geri bildirimler yüklenemedi.');
        this.qualityLoading.set(false);
      },
    });
  }

  private loadRuntimeModelSettings(): void {
    if (this.runtimeModelLoading()) return;
    this.runtimeModelLoading.set(true);
    this.runtimeModelError.set(null);
    this.api.getRuntimeModelSettings().subscribe({
      next: (settings) => {
        this.runtimeModelSettings.set(settings);
        this.runtimeModelDraft.set({ ...settings });
        this.runtimeModelLoading.set(false);
      },
      error: (response) => {
        this.runtimeModelLoading.set(false);
        this.runtimeModelError.set(response.error?.detail ?? 'Model ayarları alınamadı.');
      },
    });
  }

  private loadTopicStudy(topicId: number): void {
    this.studyLoading.set(true);
    this.studyError.set(null);
    this.studySteps.set([]);
    this.studyStepIndex.set(0);
    this.stepAssistantResponse.set(null);
    this.stepAssistantError.set(null);
    this.api.getTopicStudySteps(topicId).subscribe({
      next: (steps) => {
        this.studySteps.set(steps);
        this.progress.prune(topicId, steps.map((step) => step.id));
        const lastStepId = this.progress.topic(topicId).lastStepId;
        const pendingStepId = this.pendingStudyStepId();
        const pendingIndex = steps.findIndex((step) => step.id === pendingStepId);
        const savedIndex = steps.findIndex((step) => step.id === lastStepId);
        const initialIndex = pendingIndex >= 0 ? pendingIndex : savedIndex >= 0 ? savedIndex : 0;
        this.pendingStudyStepId.set(null);
        this.studyStepIndex.set(initialIndex);
        this.progress.visit(topicId, steps[initialIndex]?.id ?? null);
        this.studyLoading.set(false);
      },
      error: (response) => {
        this.studyLoading.set(false);
        this.studyError.set(response.error?.detail ?? 'Bu konu için öğrenme kaynakları yüklenemedi.');
      },
    });
  }

  private loadTopicPrerequisites(topicId: number): void {
    this.topicPrerequisites.set([]);
    this.api.getTopicPrerequisites(topicId).subscribe({
      next: (prerequisites) => this.topicPrerequisites.set(prerequisites),
      error: () => this.showToast('Konu ön koşulları yüklenemedi.', 'error'),
    });
  }

  private updateCurrentPage(url: string): void {
    const path = url.split('?')[0];
    const page = path === '/harita' ? 'map' : path === '/kaynaklar' ? 'sources' : path === '/kalite' ? 'quality' : path === '/ayarlar' ? 'settings' : 'home';
    this.currentPage.set(page);
    if (page === 'quality') this.loadQualityCenter();
    if (page === 'settings') this.loadRuntimeModelSettings();
  }

  private applyAnalysisState(analysis: DocumentAnalysis): void {
    if (this.documentAnalysis()?.document.id !== analysis.document.id) this.contextualEnrichment.set(false);
    this.documentAnalysis.set(analysis);
    this.documentDetails.update((items) => ({ ...items, [analysis.document.id]: analysis }));
    if (analysis.status === 'ready') {
      this.stopAnalysisPolling();
      this.approvedProposalIds.set(analysis.topics.map((topic) => topic.id));
      this.proposalDrafts.set(Object.fromEntries(analysis.topics.map((topic) => [topic.id, {
        title: topic.title,
        description: topic.description ?? '',
      }])));
      this.lessonTitleDraft.set(analysis.suggested_lesson_title ?? analysis.document.lesson_title ?? this.filenameTitle(analysis.document.source_name));
      this.lessonMergePreview.set(null);
      return;
    }
    if (analysis.status === 'failed') {
      this.stopAnalysisPolling();
      if (this.indexingDocumentId() === analysis.document.id) {
        this.indexingDocumentId.set(null);
        this.showToast(analysis.error_message ?? 'İndeksleme tamamlanamadı.', 'error');
      } else {
        this.uploadError.set(analysis.error_message ?? 'Doküman analizi tamamlanamadı.');
      }
      return;
    }
    if (analysis.status === 'approved') {
      this.stopAnalysisPolling();
      if (this.indexingDocumentId() === analysis.document.id) {
        this.indexingDocumentId.set(null);
        this.approvedProposalIds.set([]);
        this.showToast(`${analysis.document.source_name} indekslendi ve öğrenmeye hazır.`, 'success');
        this.loadWorkspace();
      }
      return;
    }
    if (analysis.status === 'processing') this.pollAnalysis(analysis.document.id);
    else this.stopAnalysisPolling();
  }

  protected documentTitle(document: SourceDocument): string {
    return document.lesson_title ?? document.source_name;
  }

  private filenameTitle(sourceName: string): string {
    return sourceName.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim();
  }

  private pollAnalysis(documentId: string): void {
    this.stopAnalysisPolling();
    this.analysisPollTimer = window.setTimeout(() => {
      this.api.getDocumentAnalysis(documentId).subscribe({
        next: (analysis) => this.applyAnalysisState(analysis),
        error: () => {
          this.uploadError.set('Analiz durumu alınamadı. Sayfayı yenileyip tekrar deneyin.');
        },
      });
    }, 1_200);
  }

  private stopAnalysisPolling(): void {
    if (this.analysisPollTimer !== null) {
      window.clearTimeout(this.analysisPollTimer);
      this.analysisPollTimer = null;
    }
  }

  private stopUploadQueuePolling(): void {
    if (this.uploadQueuePollTimer !== null) {
      window.clearTimeout(this.uploadQueuePollTimer);
      this.uploadQueuePollTimer = null;
    }
  }

  private stopConversionReviewPolling(): void {
    if (this.conversionReviewPollTimer !== null) {
      window.clearTimeout(this.conversionReviewPollTimer);
      this.conversionReviewPollTimer = null;
    }
  }

  protected dismissToast(): void {
    if (this.toastTimer !== null) window.clearTimeout(this.toastTimer);
    this.toastTimer = null;
    this.toast.set(null);
  }

  private showToast(message: string, tone: 'success' | 'error'): void {
    this.dismissToast();
    this.toast.set({ message, tone });
    this.toastTimer = window.setTimeout(() => this.dismissToast(), 5_000);
  }
}
