import { HttpClient, HttpHeaders } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { Observable } from 'rxjs';

export interface Topic {
  id: number;
  slug: string;
  title: string;
  description: string | null;
  parent_id: number | null;
  order_index: number;
}

export interface TopicPrerequisiteSuggestion { topic_id: number; title: string; reason: string; confidence: number; }

export interface TopicStudyStep {
  id: string;
  section_ids: string[];
  source_name: string;
  heading: string;
  content: string;
  content_type: 'explanation' | 'example' | 'code' | 'exercise';
  line_start: number;
  line_end: number;
}

export interface SourceViewerSection {
  id: string;
  heading_path: string;
  content: string;
  content_type: string;
  sequence: number;
  line_start: number;
  line_end: number;
  is_focus: boolean;
}

export interface SourceViewer {
  document_id: string;
  source_name: string;
  lesson_title: string | null;
  target_section_id: string;
  sections: SourceViewerSection[];
  assets: DocumentAsset[];
}

export interface QuizOption {
  id: string;
  text: string;
}

export interface TopicQuizQuestion {
  id: string;
  question: string;
  options: QuizOption[];
  correct_option_id: string;
  explanation: string;
  related_step_id: string;
}

export interface TopicQuiz {
  topic_id: number;
  questions: TopicQuizQuestion[];
}

export interface SourceDocument {
  contextual_enrichment?: boolean;
  id: string;
  logical_key: string;
  source_name: string;
  lesson_title: string | null;
  media_type: string;
  content_hash: string;
  version: number;
  status: 'processing' | 'active' | 'replaced' | 'failed';
  byte_size: number;
  created_at: string | null;
  replaced_at: string | null;
}

export interface UploadResponse {
  action: 'created' | 'replaced' | 'unchanged' | 'reindexed';
  document: SourceDocument;
  section_count: number;
}

export interface DocumentIndexJob {
  document_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  stage: string;
  progress: number;
  message: string | null;
  contextual_enrichment: boolean;
}

export interface BulkDeleteResponse {
  deleted_count: number;
}

export interface DocumentDeletionImpact {
  document_ids: string[];
  affected_topics: Topic[];
  learner_progress_count: number;
  prerequisite_relation_count: number;
  conversation_count: number;
  feedback_count: number;
}

export interface BackupInfo {
  filename: string;
  created_at: string;
  byte_size: number;
  download_url: string;
}

export interface SystemStatus {
  checked_at: string;
  status: 'ok' | 'degraded';
  database: 'ok' | 'unavailable';
  qdrant: 'ok' | 'unavailable';
  latest_backup: BackupInfo | null;
}

export type LlmProvider = 'ollama' | 'vllm';
export interface ProviderModel {
  id: string;
  label: string;
  size_bytes: number | null;
  parameter_size: string | null;
  quantization: string | null;
}
export interface ProviderDiscovery {
  provider: LlmProvider;
  base_url: string;
  reachable: boolean;
  error: string | null;
  models: ProviderModel[];
}
export interface RuntimeModelSettings {
  text_provider: LlmProvider;
  ollama_base_url: string;
  ollama_model: string;
  vllm_base_url: string;
  vllm_model: string | null;
  vision_provider: LlmProvider;
  vision_model: string;
  context_length: number;
  embedding_model: string;
  embedding_device: string;
  providers: ProviderDiscovery[];
}
export interface RuntimeModelTest { ok: boolean; message: string; text_model: string; }
export interface FeedbackState { keys: string[]; }

export interface TopicProposal {
  id: string;
  title: string;
  description: string | null;
  existing_topic_id: number | null;
  section_count: number;
  example_count: number;
  code_count: number;
  exercise_count: number;
}

export interface DocumentAnalysis {
  document: SourceDocument;
  model: string | null;
  suggested_lesson_title: string | null;
  status: 'processing' | 'ready' | 'approved' | 'failed' | 'pending';
  stage: string;
  progress: number;
  message: string | null;
  error_message: string | null;
  topics: TopicProposal[];
}

export interface DocumentAsset {
  table_index?: number | null;
  table_repair_required?: boolean;
  proposed_table?: { rows: number; columns: number; cells: unknown[]; refinement?: { status: string; cells: { row: number; column: number; before: string; after?: string; accepted: boolean; dpi: number; verification_method?: string }[]; errors: string[]; context_added: string[]; unresolved_words?: string[]; structure_review?: { issues: string[] } } } | null;
  proposed_markdown?: string | null;
  table_validation?: { status: string; message: string; word_recall: number; word_precision: number; missing_numbers: string[]; additional_numbers: string[] } | null;
  id: string;
  page_number: number | null;
  kind: string;
  description: string | null;
  confidence: number | null;
  needs_review: boolean;
  url: string;
  analysis_status: 'pending' | 'processing' | 'ready' | 'failed';
  model: string | null;
  message: string | null;
  error_message: string | null;
  structured_markdown: string | null;
  row_count: number | null;
  column_count: number | null;
  inclusion_status: 'pending' | 'accepted' | 'rejected';
}

export interface MarkdownRevision {
  id: string;
  revision_number: number;
  content_hash: string;
  byte_size: number;
  reason: string;
  created_at: string | null;
}

export interface DocumentConversion {
  formula_review_required?: boolean;
  formula_review_hash?: string | null;
  quality_report?: { requires_review?: boolean; corrected_font_characters?: number; source_page_count?: number; issues?: {type: string; pages?: number[]; text?: string}[]; formulas?: { latex: string; provenance?: { page_no: number }[]; validation?: {status: string; needs_review: boolean; reason: string; independent_latex?: string; pdf_text?: {status: string; source_text: string}} }[] };
  document: SourceDocument;
  status: 'processing' | 'ready' | 'published' | 'failed';
  markdown: string | null;
  markdown_hash: string | null;
  converter: string | null;
  converter_version: string | null;
  ocr_used: boolean;
  page_count: number | null;
  table_count: number;
  image_count: number;
  stage: string;
  progress: number;
  message: string | null;
  error_message: string | null;
  assets: DocumentAsset[];
  current_revision_number: number;
  revisions: MarkdownRevision[];
}

export interface TopicApprovalDecision {
  proposal_id: string;
  title: string;
  description: string | null;
}

export interface LessonMergePreview {
  lesson_title: string;
  matching_documents: SourceDocument[];
  overlapping_topics: string[];
}

export interface ChatSource {
  index: number;
  section_id: string | null;
  source_name: string;
  heading_path: string;
  line_start: number;
  line_end: number;
  score: number;
}

export type FeedbackKind = 'answer_helpful' | 'answer_unhelpful' | 'quiz_question_issue' | 'source_mismatch';

export interface FeedbackRequest {
  client_id: string;
  profile_id?: string;
  kind: FeedbackKind;
  topic_id?: number;
  conversation_id?: string;
  source_section_id?: string;
  payload?: Record<string, unknown>;
}

export interface TopicProgressPayload {
  completed_step_ids: string[];
  review_plans: Array<{
    stepId: string;
    heading: string;
    stage: 0 | 1 | 2;
    dueAt: number;
    ease?: number;
    correctStreak?: number;
    lastReviewedAt?: number | null;
  }>;
  last_step_id: string | null;
  last_visited_at: number;
  quiz_attempts: number;
  last_quiz_score: number | null;
  last_quiz_at: number;
}

export interface DashboardReview {
  topic_id: number;
  topic_title: string;
  step_id: string;
  heading: string;
  due_at: number;
  overdue: boolean;
}

export interface DashboardTopic {
  topic_id: number;
  title: string;
  completed_steps: number;
  total_steps: number;
  completion_percent: number;
  review_count: number;
  last_quiz_score: number | null;
  last_quiz_at: number;
  is_complete: boolean;
  is_in_progress: boolean;
}

export interface LearnerDashboard {
  display_name: string;
  total_topics: number;
  completed_topics: number;
  in_progress_topics: number;
  total_completed_steps: number;
  total_steps: number;
  today_reviews: DashboardReview[];
  in_progress: DashboardTopic[];
  recent_quizzes: DashboardTopic[];
}

export interface AuthSession {
  access_token: string;
  token_type: 'bearer';
  user_id: string;
  username: string;
  display_name: string;
}

export interface LearnerProfile {
  id: string;
  client_id: string;
  display_name: string;
  created_at: string | null;
  updated_at: string | null;
  progress: Record<string, TopicProgressPayload>;
}

export type FeedbackReviewStatus = 'new' | 'reviewed' | 'ignored';

export interface QualityFeedback {
  id: string;
  kind: FeedbackKind;
  topic_id: number | null;
  topic_title: string | null;
  source_section_id: string | null;
  source_name: string | null;
  heading_path: string | null;
  conversation_id: string | null;
  payload: Record<string, unknown> | null;
  review_status: FeedbackReviewStatus;
  review_note: string | null;
  created_at: string | null;
  reviewed_at: string | null;
}

export interface QualityOverview {
  new_count: number;
  reviewed_count: number;
  ignored_count: number;
  by_kind: Record<string, number>;
}
export interface QualityIssueMetric { key: string; label: string; count: number; }
export interface QualityInsights { reviewed_candidate_count: number; topic_issues: QualityIssueMetric[]; source_issues: QualityIssueMetric[]; }
export interface QualityExport { candidate_count: number; filename: string; download_url: string; }

export interface ChatResponse {
  conversation_id: string;
  answer: string;
  model: string;
  sources: ChatSource[];
}

export interface ConversationMessage {
  id: string;
  sequence: number;
  role: 'user' | 'assistant';
  content: string;
  model: string | null;
  created_at: string | null;
  sources: ChatSource[];
}

export interface ConversationDetail {
  id: string;
  title: string;
  topic_id: number | null;
  created_at: string | null;
  updated_at: string | null;
  messages: ConversationMessage[];
}

@Injectable({ providedIn: 'root' })
export class LearningApiService {
  private readonly http = inject(HttpClient);
  private readonly apiRoot = 'http://localhost:8000/api';
  private readonly sessionKey = 'konulab.auth-session.v2';

  get session(): AuthSession | null {
    try { return JSON.parse(localStorage.getItem(this.sessionKey) ?? 'null') as AuthSession | null; } catch { return null; }
  }

  saveSession(session: AuthSession): void { localStorage.setItem(this.sessionKey, JSON.stringify(session)); }
  clearSession(): void { localStorage.removeItem(this.sessionKey); }

  getTopics(): Observable<Topic[]> {
    return this.http.get<Topic[]>(`${this.apiRoot}/topics`);
  }

  getTopicStudySteps(topicId: number): Observable<TopicStudyStep[]> {
    return this.http.get<TopicStudyStep[]>(`${this.apiRoot}/topics/${topicId}/study`);
  }

  getTopicPrerequisites(topicId: number): Observable<Topic[]> {
    return this.http.get<Topic[]>(`${this.apiRoot}/topics/${topicId}/prerequisites`);
  }

  updateTopicPrerequisites(topicId: number, prerequisiteTopicIds: number[]): Observable<Topic[]> {
    return this.http.put<Topic[]>(`${this.apiRoot}/topics/${topicId}/prerequisites`, {
      prerequisite_topic_ids: prerequisiteTopicIds,
    });
  }

  suggestTopicPrerequisites(topicId: number): Observable<TopicPrerequisiteSuggestion[]> {
    return this.http.post<TopicPrerequisiteSuggestion[]>(`${this.apiRoot}/topics/${topicId}/prerequisite-suggestions`, {});
  }

  getSourceView(sectionId: string): Observable<SourceViewer> {
    return this.http.get<SourceViewer>(`${this.apiRoot}/sections/${sectionId}/source-view`);
  }

  generateTopicQuiz(topicId: number): Observable<TopicQuiz> {
    return this.http.post<TopicQuiz>(`${this.apiRoot}/topics/${topicId}/quiz`, {});
  }

  getDocuments(): Observable<SourceDocument[]> {
    return this.http.get<SourceDocument[]>(`${this.apiRoot}/documents`);
  }

  deleteDocument(documentId: string): Observable<void> {
    return this.http.delete<void>(`${this.apiRoot}/documents/${documentId}`);
  }

  deleteDocuments(documentIds: string[]): Observable<BulkDeleteResponse> {
    return this.http.delete<BulkDeleteResponse>(`${this.apiRoot}/documents`, {
      body: { document_ids: documentIds },
    });
  }

  getDocumentDeletionImpact(documentIds: string[]): Observable<DocumentDeletionImpact> {
    return this.http.post<DocumentDeletionImpact>(`${this.apiRoot}/documents/deletion-impact`, {
      document_ids: documentIds,
    });
  }

  getSystemStatus(): Observable<SystemStatus> {
    return this.http.get<SystemStatus>(`${this.apiRoot}/system/status`);
  }

  createBackup(): Observable<BackupInfo> {
    return this.http.post<BackupInfo>(`${this.apiRoot}/admin/backups`, {});
  }

  getFeedbackState(clientId: string, profileId: string | null): Observable<FeedbackState> {
    const params: Record<string, string> = { client_id: clientId };
    if (profileId) params['profile_id'] = profileId;
    return this.http.get<FeedbackState>(`${this.apiRoot}/feedback/state`, { params });
  }

  getRuntimeModelSettings(): Observable<RuntimeModelSettings> {
    return this.http.get<RuntimeModelSettings>(`${this.apiRoot}/settings/models`);
  }

  testRuntimeModelSettings(settings: Omit<RuntimeModelSettings, 'embedding_model' | 'embedding_device' | 'providers'>): Observable<RuntimeModelTest> {
    return this.http.post<RuntimeModelTest>(`${this.apiRoot}/settings/models/test`, settings);
  }

  updateRuntimeModelSettings(settings: Omit<RuntimeModelSettings, 'embedding_model' | 'embedding_device' | 'providers'>): Observable<RuntimeModelSettings> {
    return this.http.put<RuntimeModelSettings>(`${this.apiRoot}/settings/models`, settings);
  }

  analyzeDocument(file: File): Observable<DocumentAnalysis> {
    const body = new FormData();
    body.append('file', file, file.name);
    return this.http.post<DocumentAnalysis>(`${this.apiRoot}/documents/analyze`, body);
  }

  convertDocument(file: File): Observable<DocumentConversion> {
    const body = new FormData();
    body.append('file', file, file.name);
    return this.http.post<DocumentConversion>(`${this.apiRoot}/documents/convert`, body);
  }

  getDocumentConversion(documentId: string): Observable<DocumentConversion> {
    return this.http.get<DocumentConversion>(`${this.apiRoot}/documents/${documentId}/conversion`);
  }

  saveDocumentConversionMarkdown(documentId: string, markdown: string, expectedMarkdownHash: string): Observable<DocumentConversion> {
    return this.http.put<DocumentConversion>(`${this.apiRoot}/documents/${documentId}/conversion/markdown`, {
      markdown,
      expected_markdown_hash: expectedMarkdownHash,
    });
  }

  restoreDocumentConversionMarkdown(documentId: string, revisionNumber: number, expectedMarkdownHash: string): Observable<DocumentConversion> {
    return this.http.post<DocumentConversion>(`${this.apiRoot}/documents/${documentId}/conversion/revisions/${revisionNumber}/restore`, {
      expected_markdown_hash: expectedMarkdownHash,
    });
  }

  publishConversion(documentId: string, acceptFormulaWarnings = false, expectedMarkdownHash: string | null = null): Observable<DocumentAnalysis> {
    return this.http.post<DocumentAnalysis>(`${this.apiRoot}/documents/${documentId}/publish-conversion`, {
      confirmation: 'OGRENME_HARITASINA_DONUSTUR',
      accept_formula_warnings: acceptFormulaWarnings,
      expected_markdown_hash: expectedMarkdownHash,
    });
  }

  documentAssetUrl(asset: DocumentAsset): string {
    return new URL(asset.url, this.apiRoot).href;
  }

  formulaImageUrl(documentId: string, index: number): string {
    return `${this.apiRoot}/documents/${documentId}/formulas/${index}/image`;
  }

  downloadDocumentAsset(documentId: string, assetId: string): Observable<Blob> {
    return this.http.get(`${this.apiRoot}/documents/${documentId}/assets/${assetId}`, { responseType: 'blob' });
  }

  downloadConversionBundle(documentId: string): Observable<Blob> {
    return this.http.get(`${this.apiRoot}/documents/${documentId}/conversion/bundle`, { responseType: 'blob' });
  }

  analyzeDocumentAsset(documentId: string, assetId: string, refineOnly = false): Observable<DocumentAsset> {
    return this.http.post<DocumentAsset>(`${this.apiRoot}/documents/${documentId}/assets/${assetId}/analyze${refineOnly ? '?refine_only=true' : ''}`, {});
  }

  analyzeAllDocumentAssets(documentId: string, assetIds?: string[]): Observable<DocumentAsset[]> {
    return this.http.post<DocumentAsset[]>(`${this.apiRoot}/documents/${documentId}/assets/analyze-all`, { asset_ids: assetIds?.length ? assetIds : null });
  }

  decideDocumentAssetInclusion(documentId: string, assetId: string, decision: 'accepted' | 'rejected'): Observable<DocumentAsset> {
    return this.http.post<DocumentAsset>(`${this.apiRoot}/documents/${documentId}/assets/${assetId}/inclusion/${decision}`, {});
  }

  acceptAllDocumentAssets(documentId: string): Observable<DocumentConversion> {
    return this.http.post<DocumentConversion>(`${this.apiRoot}/documents/${documentId}/assets/accept-all`, {});
  }

  getDocumentAnalysis(documentId: string): Observable<DocumentAnalysis> {
    return this.http.get<DocumentAnalysis>(`${this.apiRoot}/documents/${documentId}/analysis`);
  }

  getIndexJob(documentId: string): Observable<DocumentIndexJob | null> {
    return this.http.get<DocumentIndexJob | null>(`${this.apiRoot}/documents/${documentId}/index-job`);
  }

  startIndexJob(documentId: string, contextualEnrichment: boolean): Observable<DocumentIndexJob> {
    return this.http.post<DocumentIndexJob>(`${this.apiRoot}/documents/${documentId}/index-job`, { contextual_enrichment: contextualEnrichment });
  }

  reanalyzeDocument(documentId: string): Observable<DocumentAnalysis> {
    return this.http.post<DocumentAnalysis>(`${this.apiRoot}/documents/${documentId}/reanalyze`, {});
  }

  previewLessonMerge(documentId: string, lessonTitle: string, decisions: TopicApprovalDecision[]): Observable<LessonMergePreview> {
    return this.http.post<LessonMergePreview>(`${this.apiRoot}/documents/${documentId}/lesson-merge-preview`, {
      lesson_title: lessonTitle,
      decisions,
    });
  }

  approveDocumentAnalysis(documentId: string, lessonTitle: string, decisions: TopicApprovalDecision[], allowExistingLessonMerge = false, contextualEnrichment = false): Observable<DocumentAnalysis> {
    return this.http.post<DocumentAnalysis>(`${this.apiRoot}/documents/${documentId}/approve-analysis`, {
      lesson_title: lessonTitle,
      allow_existing_lesson_merge: allowExistingLessonMerge,
      contextual_enrichment: contextualEnrichment,
      decisions,
    });
  }

  chat(
    message: string,
    topicId: number | undefined,
    conversationId: string | null,
    options: { pinnedSectionIds?: string[]; learningMode?: boolean } = {},
  ): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.apiRoot}/chat`, {
      message,
      topic_id: topicId,
      conversation_id: conversationId,
      source_limit: 6,
      pinned_section_ids: options.pinnedSectionIds ?? [],
      learning_mode: options.learningMode ?? false,
    }, this.authOptions());
  }

  getConversation(conversationId: string): Observable<ConversationDetail> {
    return this.http.get<ConversationDetail>(`${this.apiRoot}/conversations/${conversationId}`, this.authOptions());
  }

  sendFeedback(request: FeedbackRequest): Observable<{ id: string }> {
    return this.http.post<{ id: string }>(`${this.apiRoot}/feedback`, request);
  }

  bootstrapProfile(clientId: string, localProgress: Record<string, TopicProgressPayload>): Observable<LearnerProfile> {
    return this.http.post<LearnerProfile>(`${this.apiRoot}/profiles/bootstrap`, {
      client_id: clientId,
      local_progress: localProgress,
    });
  }

  updateProfile(profileId: string, clientId: string, displayName: string): Observable<LearnerProfile> {
    return this.http.patch<LearnerProfile>(`${this.apiRoot}/profiles/${profileId}`, {
      client_id: clientId,
      display_name: displayName,
    });
  }

  saveTopicProgress(
    profileId: string,
    topicId: number,
    clientId: string,
    progress: TopicProgressPayload,
  ): Observable<TopicProgressPayload> {
    return this.http.put<TopicProgressPayload>(`${this.apiRoot}/profiles/${profileId}/progress/${topicId}`, {
      client_id: clientId,
      ...progress,
    });
  }

  getLearnerDashboard(profileId: string, clientId: string): Observable<LearnerDashboard> {
    return this.http.get<LearnerDashboard>(`${this.apiRoot}/profiles/${profileId}/dashboard`, {
      params: { client_id: clientId },
    });
  }

  register(username: string, displayName: string, password: string, profileId: string | null, clientId: string): Observable<AuthSession> {
    return this.http.post<AuthSession>(`${this.apiRoot}/auth/register`, {
      username, display_name: displayName, password, profile_id: profileId, client_id: clientId,
    });
  }

  login(username: string, password: string, profileId: string | null, clientId: string): Observable<AuthSession> {
    return this.http.post<AuthSession>(`${this.apiRoot}/auth/login`, {
      username, password, profile_id: profileId, client_id: clientId,
    });
  }

  logout(): Observable<void> {
    return this.http.post<void>(`${this.apiRoot}/auth/logout`, {}, this.authOptions());
  }

  private authOptions(): { headers?: HttpHeaders } {
    const token = this.session?.access_token;
    return token ? { headers: new HttpHeaders({ Authorization: `Bearer ${token}` }) } : {};
  }

  getQualityOverview(): Observable<QualityOverview> {
    return this.http.get<QualityOverview>(`${this.apiRoot}/quality/overview`);
  }

  getQualityFeedback(reviewStatus: FeedbackReviewStatus | 'all' = 'new'): Observable<QualityFeedback[]> {
    return this.http.get<QualityFeedback[]>(`${this.apiRoot}/quality/feedback`, {
      params: { review_status: reviewStatus },
    });
  }

  reviewQualityFeedback(
    feedbackId: string,
    reviewStatus: FeedbackReviewStatus,
    reviewNote: string | null,
  ): Observable<QualityFeedback> {
    return this.http.patch<QualityFeedback>(`${this.apiRoot}/quality/feedback/${feedbackId}`, {
      review_status: reviewStatus,
      review_note: reviewNote,
    });
  }

  getQualityInsights(): Observable<QualityInsights> { return this.http.get<QualityInsights>(`${this.apiRoot}/quality/insights`); }
  exportReviewedFeedback(): Observable<QualityExport> { return this.http.post<QualityExport>(`${this.apiRoot}/quality/export-reviewed`, {}); }
}
