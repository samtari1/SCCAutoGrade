import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const RUNNING_STATUSES = new Set(["queued", "started", "deferred", "scheduled", "submitting", "pending"]);
const STOPPABLE_STATUSES = new Set(["queued", "started", "deferred", "scheduled", "pending"]);
const ROUTES = {
  login: "login",
  register: "register",
  grader: "grader",
  history: "history"
};

function routeFromHash() {
  const hash = window.location.hash;
  if (hash === "#/login") return ROUTES.login;
  if (hash === "#/register") return ROUTES.register;
  if (hash === "#/history") return ROUTES.history;
  return ROUTES.grader;
}

function relTime(unixTs) {
  const diff = Date.now() / 1000 - unixTs;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function StatusBadge({ status }) {
  let cls = "badge ";
  if (RUNNING_STATUSES.has(status)) cls += "badge-running";
  else if (status === "finished") cls += "badge-done";
  else if (status === "failed") cls += "badge-fail";
  else cls += "badge-idle";
  const label = RUNNING_STATUSES.has(status) ? "running" : status;
  return <span className={cls}>{label}</span>;
}

function DropZone({ label, accept, file, onFile, reusableHint }) {
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) onFile(dropped);
  }

  const classes = ["drop-zone", dragOver && "drop-zone-over", file && "drop-zone-filled"]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inputRef.current?.click(); } }}
      role="button"
      tabIndex={0}
      aria-label={label}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        style={{ display: "none" }}
        onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.target.value = ""; }}
      />
      <span className="drop-zone-icon">{file ? "✅" : "📂"}</span>
      <span className="drop-zone-cta">{label}</span>
      {file
        ? <span className="drop-zone-filename">{file.name}</span>
        : <span className="drop-zone-hint">drag &amp; drop or click to browse</span>
      }
      {!file && reusableHint && <span className="reuse-hint">{reusableHint}</span>}
    </div>
  );
}

function App() {
  const [page, setPage] = useState(routeFromHash);
  const [authToken, setAuthToken] = useState(() => localStorage.getItem("ag_auth_token") || "");
  const [authUser, setAuthUser] = useState(null);
  const [authPending, setAuthPending] = useState(false);
  const [authError, setAuthError] = useState("");
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [registerEmail, setRegisterEmail] = useState("");
  const [registerPassword, setRegisterPassword] = useState("");
  const [registerConfirmPassword, setRegisterConfirmPassword] = useState("");
  const [showRegisterPassword, setShowRegisterPassword] = useState(false);
  const [mainZip, setMainZip] = useState(null);
  const [instructionsHtml, setInstructionsHtml] = useState(null);
  const [instructionsText, setInstructionsText] = useState("");
  const [reusableInputs, setReusableInputs] = useState(null);
  const [submissionOptions, setSubmissionOptions] = useState([]);
  const [selectedSubmissions, setSelectedSubmissions] = useState([]);
  const [loadingSubmissions, setLoadingSubmissions] = useState(false);
  const [jobId, setJobId] = useState(() => localStorage.getItem("ag_job_id") || "");
  const [status, setStatus] = useState(() => localStorage.getItem("ag_job_id") ? "queued" : "idle");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [artifacts, setArtifacts] = useState([]);
  const [evaluatorKey, setEvaluatorKey] = useState("auto");
  const [activeEvaluator, setActiveEvaluator] = useState("");
  const [routeType, setRouteType] = useState("");
  const [routingReason, setRoutingReason] = useState("");
  const [confidence, setConfidence] = useState(null);
  const [streamLines, setStreamLines] = useState([]);
  const [streamConnected, setStreamConnected] = useState(false);
  const [autoScrollStream, setAutoScrollStream] = useState(true);
  const [multiAgentGrading, setMultiAgentGrading] = useState(true);
  const [disagreementThreshold, setDisagreementThreshold] = useState("5.0");
  const [partDisagreementThreshold, setPartDisagreementThreshold] = useState("10.0");
  const [gradingContext, setGradingContext] = useState(
    "In the overall feedback, try to make it conversational and friendly. Try to acknowledge the strengths before pointing out the weaknesses. When pointing out the weaknesses don't be too harsh."
  );
  const [availableEvaluators, setAvailableEvaluators] = useState([]);
  const [reportSortMode, setReportSortMode] = useState("date-desc");
  const [resumingJobId, setResumingJobId] = useState(null);
  const streamPanelRef = useRef(null);
  const statusSectionRef = useRef(null);

  // History state
  const [jobHistory, setJobHistory] = useState([]);
  const [expandedJobId, setExpandedJobId] = useState(null);
  const [historyArtifacts, setHistoryArtifacts] = useState({});
  const [reportUrl, setReportUrl] = useState(null);
  const [reportTitle, setReportTitle] = useState("");
  const [cancelingJobIds, setCancelingJobIds] = useState({});
  const [selectedJobIds, setSelectedJobIds] = useState(new Set());
  const [deletingJobIds, setDeletingJobIds] = useState({});
  const [bulkDeleting, setBulkDeleting] = useState(false);

  const withToken = useCallback((url) => {
    if (!authToken) {
      return url;
    }
    const sep = url.includes("?") ? "&" : "?";
    return `${url}${sep}token=${encodeURIComponent(authToken)}`;
  }, [authToken]);

  const apiFetch = useCallback(async (path, options = {}) => {
    const headers = {
      ...(options.headers || {}),
      ...(authToken ? { Authorization: `Bearer ${authToken}` } : {}),
    };
    const res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers,
    });
    if (res.status === 401) {
      setAuthToken("");
      setAuthUser(null);
      setPage(ROUTES.login);
      window.location.hash = "#/login";
      throw new Error("Session expired. Please log in again.");
    }
    return res;
  }, [authToken]);

  useEffect(() => {
    const onHashChange = () => setPage(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    onHashChange();
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function navigateTo(nextPage) {
    const nextHash =
      nextPage === ROUTES.history
        ? "#/history"
        : nextPage === ROUTES.login
          ? "#/login"
          : nextPage === ROUTES.register
            ? "#/register"
            : "#/";
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
    setPage(nextPage);
  }

  useEffect(() => {
    if (authToken) {
      localStorage.setItem("ag_auth_token", authToken);
      return;
    }
    localStorage.removeItem("ag_auth_token");
  }, [authToken]);

  useEffect(() => {
    if (!authToken) {
      setAuthUser(null);
      setJobId("");
      setStatus("idle");
      setReusableInputs(null);
      setSubmissionOptions([]);
      setSelectedSubmissions([]);
      localStorage.removeItem("ag_job_id");
      if (![ROUTES.login, ROUTES.register].includes(page)) {
        navigateTo(ROUTES.login);
      }
      return;
    }

    let cancelled = false;
    const loadMe = async () => {
      try {
        const res = await apiFetch("/api/auth/me");
        if (!res.ok) {
          throw new Error("Unable to verify session");
        }
        const data = await res.json();
        if (!cancelled) {
          setAuthUser(data.user || null);
          if ([ROUTES.login, ROUTES.register].includes(page)) {
            navigateTo(ROUTES.grader);
          }
        }
      } catch {
        if (!cancelled) {
          setAuthToken("");
          setAuthUser(null);
          navigateTo(ROUTES.login);
        }
      }
    };

    loadMe();
    return () => {
      cancelled = true;
    };
  }, [apiFetch, authToken, page]);

  useEffect(() => {
    if (!authToken) {
      setAvailableEvaluators([]);
      return;
    }
    const loadEvaluators = async () => {
      try {
        const res = await apiFetch("/api/evaluators");
        if (!res.ok) {
          return;
        }
        const data = await res.json();
        const items = Array.isArray(data.evaluators) ? data.evaluators : [];
        setAvailableEvaluators(items.map((item) => item.key));
      } catch {
        // Keep default selection if endpoint is unavailable.
      }
    };
    loadEvaluators();
  }, [apiFetch, authToken]);

  // Persist current job ID across page refreshes.
  useEffect(() => {
    if (!authToken) {
      localStorage.removeItem("ag_job_id");
      return;
    }
    if (jobId) {
      localStorage.setItem("ag_job_id", jobId);
    }
  }, [authToken, jobId]);

  useEffect(() => {
    if (!authToken) {
      setReusableInputs(null);
      return;
    }
    if (!jobId) {
      setReusableInputs(null);
      return;
    }

    let isCancelled = false;
    const loadReusableInputs = async () => {
      try {
        const res = await apiFetch(`/api/jobs/${jobId}/input-info`);
        if (!res.ok) {
          if (!isCancelled) {
            setReusableInputs(null);
          }
          return;
        }
        const data = await res.json();
        if (!isCancelled) {
          setReusableInputs(data);
        }
      } catch {
        if (!isCancelled) {
          setReusableInputs(null);
        }
      }
    };

    loadReusableInputs();
    return () => {
      isCancelled = true;
    };
  }, [apiFetch, authToken, jobId, status]);

  useEffect(() => {
    if (!authToken) {
      setSubmissionOptions([]);
      setSelectedSubmissions([]);
      setLoadingSubmissions(false);
      return;
    }
    if (!mainZip && !reusableInputs?.job_id) {
      setSubmissionOptions([]);
      setSelectedSubmissions([]);
      setLoadingSubmissions(false);
      return;
    }

    let isCancelled = false;
    const loadSubmissionPreview = async () => {
      setLoadingSubmissions(true);
      try {
        const form = new FormData();
        if (mainZip) {
          form.append("main_zip", mainZip);
        } else if (reusableInputs?.job_id) {
          form.append("reuse_job_id", reusableInputs.job_id);
        }

        const res = await apiFetch("/api/submissions/preview", {
          method: "POST",
          body: form
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || "Failed to preview submissions");
        }

        if (!isCancelled) {
          const submissions = Array.isArray(data.submissions) ? data.submissions : [];
          const selected = Array.isArray(data.selected_students) ? data.selected_students : submissions;
          setSubmissionOptions(submissions);
          setSelectedSubmissions(selected);
        }
      } catch (err) {
        if (!isCancelled) {
          setSubmissionOptions([]);
          setSelectedSubmissions([]);
          setError((prev) => prev || err.message || "Failed to preview submissions");
        }
      } finally {
        if (!isCancelled) {
          setLoadingSubmissions(false);
        }
      }
    };

    loadSubmissionPreview();
    return () => {
      isCancelled = true;
    };
  }, [apiFetch, authToken, mainZip, reusableInputs]);

  const fetchHistory = useCallback(async () => {
    if (!authToken) {
      setJobHistory([]);
      return [];
    }
    try {
      const res = await apiFetch("/api/jobs");
      if (!res.ok) return [];
      const data = await res.json();
      const jobs = data.jobs || [];
      setJobHistory(jobs);

      // Keep the currently expanded row in sync while grading is ongoing.
      if (expandedJobId) {
        const expandedJob = jobs.find((job) => job.job_id === expandedJobId);
        if (expandedJob) {
          const expectedHtmlCount = Number(expandedJob.artifact_count || 0);
          const cachedFiles = historyArtifacts[expandedJobId] || [];
          const cachedHtmlCount = cachedFiles.filter((file) => file.endsWith(".html")).length;

          if (expectedHtmlCount > cachedHtmlCount) {
            try {
              const artifactsRes = await apiFetch(`/api/jobs/${expandedJobId}/artifacts`);
              if (artifactsRes.ok) {
                const artifactsData = await artifactsRes.json();
                setHistoryArtifacts((prev) => ({
                  ...prev,
                  [expandedJobId]: artifactsData.files || []
                }));
              }
            } catch {
              // Non-blocking: keep existing cached history artifacts.
            }
          }
        }
      }

      return jobs;
    } catch {
      // history panel is non-critical
      return [];
    }
  }, [apiFetch, authToken, expandedJobId, historyArtifacts]);

  const syncCurrentJobFromHistory = useCallback(async (targetJobId) => {
    if (!authToken) {
      return;
    }
    const currentId = targetJobId || jobId;
    if (!currentId) {
      return;
    }

    const jobs = await fetchHistory();
    const matched = jobs.find((job) => job.job_id === currentId);
    if (!matched) {
      setStatus("unknown");
      setMessage("Job not found in active queue. It may have expired from Redis.");
      return;
    }

    setStatus(matched.status || "unknown");
    setActiveEvaluator(matched.evaluator_key || "");
    setRouteType(matched.route_type || "");
    if (["finished", "failed", "canceled", "stopped"].includes(matched.status)) {
      setError("");
    }

    // Pull artifacts for completed jobs so reports still show on refresh/reopen.
    if ((matched.artifact_count || 0) > 0) {
      try {
        const res = await apiFetch(`/api/jobs/${currentId}/artifacts`);
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.files)) {
            setArtifacts(data.files);
          }
        }
      } catch {
        // Keep existing artifacts if artifact fetch fails.
      }
    }
  }, [apiFetch, authToken, fetchHistory, jobId]);

  useEffect(() => {
    if (page !== ROUTES.history) {
      return;
    }

    fetchHistory();
    const timer = setInterval(fetchHistory, 10000);
    return () => clearInterval(timer);
  }, [fetchHistory, page]);

  const instructionsProvided = Boolean(instructionsHtml || instructionsText.trim());
  const canSubmit = useMemo(
    () => Boolean(
      ((mainZip && instructionsProvided) || reusableInputs) &&
      (submissionOptions.length === 0 || selectedSubmissions.length > 0)
    ),
    [mainZip, instructionsProvided, reusableInputs, submissionOptions.length, selectedSubmissions.length]
  );

  useEffect(() => {
    if (!authToken) {
      return;
    }
    if (!jobId || ["finished", "failed", "stopped", "canceled"].includes(status) || streamConnected) {
      return;
    }

    const timer = setInterval(async () => {
      try {
        const res = await apiFetch(`/api/jobs/${jobId}`);
        if (!res.ok) {
          if (res.status === 404) {
            await syncCurrentJobFromHistory(jobId);
            return;
          }
          throw new Error(`Failed to fetch job status (${res.status})`);
        }
        const data = await res.json();
        setStatus(data.status || "unknown");
        setMessage(data.message || "");
        setError(data.error || "");
        if (Array.isArray(data.artifact_files)) {
          setArtifacts(data.artifact_files);
        }
      } catch (err) {
        setError(err.message || "Unknown polling error");
      }
    }, 2000);

    return () => clearInterval(timer);
  }, [apiFetch, authToken, jobId, status, streamConnected, syncCurrentJobFromHistory]);

  useEffect(() => {
    if (!authToken) {
      return;
    }
    if (page !== ROUTES.grader || !jobId) {
      return;
    }
    if (!["finished", "failed", "stopped", "canceled"].includes(status)) {
      return;
    }

    let isCancelled = false;
    const loadArtifactsForTerminalJob = async () => {
      try {
        const res = await apiFetch(`/api/jobs/${jobId}/artifacts`);
        if (!res.ok) {
          return;
        }
        const data = await res.json();
        if (!isCancelled && Array.isArray(data.files)) {
          setArtifacts(data.files);
        }
      } catch {
        // Non-blocking: keep current artifacts state.
      }
    };

    loadArtifactsForTerminalJob();
    return () => {
      isCancelled = true;
    };
  }, [apiFetch, authToken, page, jobId, status]);

  useEffect(() => {
    if (!authToken) {
      setStreamConnected(false);
      return;
    }
    if (!jobId || ["finished", "failed", "stopped", "canceled"].includes(status)) {
      setStreamConnected(false);
      return;
    }

    const stream = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream?token=${encodeURIComponent(authToken)}`);

    stream.onopen = () => {
      setStreamConnected(true);
    };

    stream.addEventListener("log", (event) => {
      setStreamLines((prev) => {
        const next = [...prev, event.data];
        return next.slice(-600);
      });
    });

    stream.addEventListener("status", (event) => {
      try {
        const data = JSON.parse(event.data);
        setStatus(data.status || "unknown");
        setMessage(data.message || "");
        setError(data.error || "");
        setActiveEvaluator(data.evaluator_key || "");
        setRouteType(data.route_type || "");
        setRoutingReason(data.routing_reason || "");
        setConfidence(data.confidence || null);
        if (Array.isArray(data.artifact_files)) {
          setArtifacts(data.artifact_files);
        }
        if (["finished", "failed", "stopped", "canceled"].includes(data.status)) {
          stream.close();
          fetchHistory();
        }
      } catch {
        // Ignore malformed stream packets.
      }
    });

    stream.onerror = () => {
      setStreamConnected(false);
      // Do not close manually; EventSource will auto-reconnect.
      setError((prev) => prev || "Live stream disconnected temporarily. Retrying...");

      // If this is an old job no longer present in Redis, reconcile from disk-backed history.
      if (RUNNING_STATUSES.has(status)) {
        syncCurrentJobFromHistory(jobId);
      }
    };

    return () => {
      setStreamConnected(false);
      stream.close();
    };
  }, [authToken, jobId, status, syncCurrentJobFromHistory]);

  useEffect(() => {
    if (!autoScrollStream || !streamPanelRef.current) {
      return;
    }
    streamPanelRef.current.scrollTop = streamPanelRef.current.scrollHeight;
  }, [streamLines, autoScrollStream]);

  async function submitJob(event) {
    event.preventDefault();
    setError("");
    setArtifacts([]);
    setStreamLines([]);
    setStatus("submitting");
    setActiveEvaluator("");
    setRouteType("");
    setRoutingReason("");
    setConfidence(null);

    const form = new FormData();
    if (mainZip && instructionsProvided) {
      form.append("main_zip", mainZip);
      if (instructionsHtml) {
        form.append("instructions_html", instructionsHtml);
      } else {
        // Convert pasted text → HTML blob so the backend always receives an HTML file
        const escaped = instructionsText
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;");
        const htmlContent = `<html><body><pre>${escaped}</pre></body></html>`;
        const blob = new Blob([htmlContent], { type: "text/html" });
        form.append("instructions_html", new File([blob], "instructions.html", { type: "text/html" }));
      }
    } else if (reusableInputs?.job_id) {
      form.append("reuse_job_id", reusableInputs.job_id);
    } else {
      throw new Error("Select both input files or reuse stored files from an unfinished job");
    }
    if (submissionOptions.length > 0) {
      form.append("selected_students", JSON.stringify(selectedSubmissions));
    }
    form.append("evaluator_key", evaluatorKey);
    form.append("multi_agent_grading", String(multiAgentGrading));
    form.append("multi_agent_disagreement_threshold", disagreementThreshold || "5.0");
    form.append("multi_agent_part_disagreement_threshold", partDisagreementThreshold || "10.0");
    form.append("grading_context", gradingContext);

    try {
      const res = await apiFetch("/api/jobs", {
        method: "POST",
        body: form
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to create job");
      }

      setJobId(data.job_id);
      setStatus(data.status || "queued");
      setMessage("Job submitted");
      setActiveEvaluator(data.evaluator_key || "");
      setRouteType(data.route_type || "");
      setRoutingReason(data.routing_reason || "");
      statusSectionRef.current?.scrollIntoView({ behavior: "smooth" });
    } catch (err) {
      setStatus("failed");
      setError(err.message || "Unknown submit error");
    }
  }

  async function toggleExpandJob(jid) {
    if (expandedJobId === jid) {
      setExpandedJobId(null);
      return;
    }
    setExpandedJobId(jid);
    if (!historyArtifacts[jid]) {
      try {
        const res = await apiFetch(`/api/jobs/${jid}/artifacts`);
        if (res.ok) {
          const data = await res.json();
          setHistoryArtifacts((prev) => ({ ...prev, [jid]: data.files || [] }));
        }
      } catch {
        setHistoryArtifacts((prev) => ({ ...prev, [jid]: [] }));
      }
    }
  }

  function openReport(jid, filename) {
    setReportUrl(withToken(`${API_BASE}/api/jobs/${jid}/artifacts/${encodeURIComponent(filename)}`));
    setReportTitle(filename);
  }

  async function stopJob(targetJobId) {
    if (!targetJobId || cancelingJobIds[targetJobId]) {
      return;
    }

    setCancelingJobIds((prev) => ({ ...prev, [targetJobId]: true }));
    try {
      const res = await apiFetch(`/api/jobs/${targetJobId}/cancel`, {
        method: "POST"
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to stop job");
      }

      if (targetJobId === jobId) {
        if (data.status) {
          setStatus(data.status);
        }
        if (data.message) {
          setMessage(data.message);
        }
      }
      fetchHistory();
    } catch (err) {
      if (targetJobId === jobId) {
        setError(err.message || "Failed to stop job");
      }
    } finally {
      setCancelingJobIds((prev) => {
        const next = { ...prev };
        delete next[targetJobId];
        return next;
      });
    }
  }

  async function deleteJob(targetJobId) {
    if (!targetJobId || deletingJobIds[targetJobId]) return;
    setDeletingJobIds((prev) => ({ ...prev, [targetJobId]: true }));
    try {
      const res = await apiFetch(`/api/jobs/${targetJobId}`, { method: "DELETE" });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to delete job");
      }
      if (targetJobId === jobId) {
        setJobId(null);
        setStatus(null);
      }
      setSelectedJobIds((prev) => { const next = new Set(prev); next.delete(targetJobId); return next; });
      fetchHistory();
    } catch (err) {
      alert(err.message || "Failed to delete job");
    } finally {
      setDeletingJobIds((prev) => { const next = { ...prev }; delete next[targetJobId]; return next; });
    }
  }

  async function deleteSelectedJobs() {
    if (selectedJobIds.size === 0 || bulkDeleting) return;
    setBulkDeleting(true);
    try {
      const res = await apiFetch("/api/jobs", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_ids: Array.from(selectedJobIds) }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Failed to delete jobs");
      const deletedSet = new Set(data.deleted || []);
      if (deletedSet.has(jobId)) {
        setJobId(null);
        setStatus(null);
      }
      setSelectedJobIds(new Set());
      fetchHistory();
    } catch (err) {
      alert(err.message || "Failed to delete jobs");
    } finally {
      setBulkDeleting(false);
    }
  }

  function toggleSelectJob(jid) {
    setSelectedJobIds((prev) => {
      const next = new Set(prev);
      if (next.has(jid)) next.delete(jid); else next.add(jid);
      return next;
    });
  }

  function toggleSelectAll() {
    if (selectedJobIds.size === jobHistory.length) {
      setSelectedJobIds(new Set());
    } else {
      setSelectedJobIds(new Set(jobHistory.map((j) => j.job_id)));
    }
  }

  async function resumeJob(targetJobId) {
    if (!targetJobId || resumingJobId) {
      return;
    }

    setResumingJobId(targetJobId);
    try {
      const res = await apiFetch(`/api/jobs/${targetJobId}/resume`, {
        method: "POST"
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Failed to resume job");
      }

      if (targetJobId === jobId) {
        setStatus("queued");
        setMessage(data.message || `Resuming job - ${data.completed_students_count || 0} students already completed`);
        setArtifacts([]);
        setStreamLines([]);
        
        setTimeout(() => {
          statusSectionRef.current?.scrollIntoView({ behavior: "smooth" });
        }, 100);
      }
      fetchHistory();
    } catch (err) {
      if (targetJobId === jobId) {
        setError(err.message || "Failed to resume job");
      }
    } finally {
      setResumingJobId(null);
    }
  }

  async function openJobInGrader(job) {
    if (!job?.job_id) {
      return;
    }

    setMainZip(null);
    setInstructionsHtml(null);
    setInstructionsText("");
    setSubmissionOptions([]);
    setSelectedSubmissions([]);
    setLoadingSubmissions(true);
    setExpandedJobId(null);

    setJobId(job.job_id);
    setStatus(job.status || "unknown");
    setActiveEvaluator(job.evaluator_key || "");
    setRouteType(job.route_type || "");
    // Keep current artifacts visible until the selected job artifacts are loaded.
    setStreamLines([]);
    setError("");
    setMessage(`Loaded job ${job.job_id}. You can rerun all students or uncheck some to regrade selected submissions.`);

    try {
      const res = await apiFetch(`/api/jobs/${job.job_id}/artifacts`);
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data.files)) {
          setArtifacts(data.files);
        }
      }
    } catch {
      // Non-blocking: grader page can still be opened without preloaded reports.
    }

    try {
      const form = new FormData();
      form.append("reuse_job_id", job.job_id);
      const res = await apiFetch("/api/submissions/preview", {
        method: "POST",
        body: form
      });
      if (res.ok) {
        const data = await res.json();
        const submissions = Array.isArray(data.submissions) ? data.submissions : [];
        const selected = Array.isArray(data.selected_students) ? data.selected_students : submissions;
        setSubmissionOptions(submissions);
        setSelectedSubmissions(selected);
      }
    } catch {
      // Non-blocking: user can still load manual files.
    } finally {
      setLoadingSubmissions(false);
    }

    navigateTo(ROUTES.grader);
    setTimeout(() => {
      statusSectionRef.current?.scrollIntoView({ behavior: "smooth" });
    }, 100);
  }

  async function submitLogin(event) {
    event.preventDefault();
    setAuthPending(true);
    setAuthError("");
    try {
      const res = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: loginEmail, password: loginPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Login failed");
      }
      setAuthToken(data.token || "");
      setAuthUser(data.user || null);
      setLoginPassword("");
      navigateTo(ROUTES.grader);
    } catch (err) {
      setAuthError(err.message || "Login failed");
    } finally {
      setAuthPending(false);
    }
  }

  async function submitRegister(event) {
    event.preventDefault();
    if (registerPassword !== registerConfirmPassword) {
      setAuthError("Passwords do not match");
      return;
    }
    setAuthPending(true);
    setAuthError("");
    try {
      const res = await fetch(`${API_BASE}/api/auth/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: registerEmail, password: registerPassword }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Registration failed");
      }
      setAuthToken(data.token || "");
      setAuthUser(data.user || null);
      setRegisterPassword("");
      setRegisterConfirmPassword("");
      navigateTo(ROUTES.grader);
    } catch (err) {
      setAuthError(err.message || "Registration failed");
    } finally {
      setAuthPending(false);
    }
  }

  async function logout() {
    try {
      if (authToken) {
        await apiFetch("/api/auth/logout", { method: "POST" });
      }
    } catch {
      // Best effort: clear local session even if logout request fails.
    }
    setAuthToken("");
    setAuthUser(null);
    setJobId("");
    localStorage.removeItem("ag_job_id");
    navigateTo(ROUTES.login);
  }

  const isActiveJob = RUNNING_STATUSES.has(status);
  const canStopActiveJob = Boolean(jobId) && STOPPABLE_STATUSES.has(status);

  if (!authToken || !authUser) {
    const isLogin = page !== ROUTES.register;
    return (
      <main className="page">
        <section className="hero auth-hero">
          <p className="hero-kicker">Faculty Access</p>
          <h1>SCC AutoGrade Platform</h1>
          <p>Sign in to access your grading jobs, reports, and history. Each account sees only its own workspace.</p>
        </section>

        <section className="card auth-card">
          <div className="auth-tabs">
            <button
              type="button"
              className={isLogin ? "btn-ghost nav-btn nav-btn-active" : "btn-ghost nav-btn"}
              onClick={() => navigateTo(ROUTES.login)}
            >
              Login
            </button>
            <button
              type="button"
              className={!isLogin ? "btn-ghost nav-btn nav-btn-active" : "btn-ghost nav-btn"}
              onClick={() => navigateTo(ROUTES.register)}
            >
              Register
            </button>
          </div>

          {isLogin ? (
            <form onSubmit={submitLogin} className="form auth-form">
              <label>
                Email
                <input type="email" value={loginEmail} onChange={(e) => setLoginEmail(e.target.value)} required />
              </label>
              <label>
                Password
                <input type="password" value={loginPassword} onChange={(e) => setLoginPassword(e.target.value)} required />
              </label>
              {authError && <p className="error">{authError}</p>}
              <button type="submit" disabled={authPending}>{authPending ? "Signing in..." : "Sign In"}</button>
            </form>
          ) : (
            <form onSubmit={submitRegister} className="form auth-form">
              <label>
                Email
                <input type="email" value={registerEmail} onChange={(e) => setRegisterEmail(e.target.value)} required />
              </label>
              <label>
                Password (minimum 8 characters)
                <input
                  type={showRegisterPassword ? "text" : "password"}
                  minLength={8}
                  value={registerPassword}
                  onChange={(e) => setRegisterPassword(e.target.value)}
                  required
                />
              </label>
              <label>
                Re-enter Password
                <input
                  type={showRegisterPassword ? "text" : "password"}
                  minLength={8}
                  value={registerConfirmPassword}
                  onChange={(e) => setRegisterConfirmPassword(e.target.value)}
                  required
                />
              </label>
              <label className="inline-checkbox">
                <input
                  type="checkbox"
                  checked={showRegisterPassword}
                  onChange={(e) => setShowRegisterPassword(e.target.checked)}
                />
                Show passwords
              </label>
              {authError && <p className="error">{authError}</p>}
              <button type="submit" disabled={authPending}>{authPending ? "Creating account..." : "Create Account"}</button>
            </form>
          )}
        </section>
      </main>
    );
  }

  return (
    <main className="page">
      {reportUrl && (
        <div className="report-backdrop" onClick={() => setReportUrl(null)}>
          <div className="report-modal" onClick={(e) => e.stopPropagation()}>
            <div className="report-modal-bar">
              <span className="report-modal-title">{reportTitle}</span>
              <button className="btn-ghost" onClick={() => setReportUrl(null)}>✕ Close</button>
            </div>
            <iframe
              src={reportUrl}
              className="report-frame"
              title={reportTitle}
              sandbox="allow-same-origin allow-scripts allow-popups"
            />
          </div>
        </div>
      )}

      <section className="hero">
        <p className="hero-kicker">Grading Operations</p>
        <h1>SCC AutoGrade Center</h1>
        <p>
          {page === ROUTES.grader
            ? "Launch a grading run with your submissions and instructions, then follow every step live."
            : "Review every grading batch, reopen reports, and jump any run back into the Grader."}
        </p>
      </section>

      <section className="card nav-card">
        <div className="auth-toolbar">
          <span className="auth-user">Signed in as {authUser?.email}</span>
          <button type="button" className="btn-ghost btn-sm" onClick={logout}>Log out</button>
        </div>
          <button
            type="button"
            className={page === ROUTES.grader ? "btn-ghost nav-btn nav-btn-active" : "btn-ghost nav-btn"}
            onClick={() => navigateTo(ROUTES.grader)}
            aria-current={page === ROUTES.grader ? "page" : undefined}
            aria-selected={page === ROUTES.grader}
          >
            Grading
          </button>
          <button
            type="button"
            className={page === ROUTES.history ? "btn-ghost nav-btn nav-btn-active" : "btn-ghost nav-btn"}
            onClick={() => navigateTo(ROUTES.history)}
            aria-current={page === ROUTES.history ? "page" : undefined}
            aria-selected={page === ROUTES.history}
          >
            History
          </button>
      </section>

      {page === ROUTES.grader && (
      <section className="card">
        <form onSubmit={submitJob} className="form">

          <div className="upload-steps">
            {/* Step 1 — ZIP */}
            <div className="upload-step">
              <div className="upload-step-header">
                <span className={`upload-step-number${mainZip ? " upload-step-number-done" : ""}`}>{mainZip ? "✅" : "1"}</span>
                <span className="upload-step-title">Submissions ZIP</span>
              </div>
              <DropZone
                label="Drop ZIP here or click to browse"
                accept=".zip"
                file={mainZip}
                onFile={setMainZip}
                reusableHint={!mainZip && reusableInputs?.main_zip_name ? `Stored: ${reusableInputs.main_zip_name}` : null}
              />
              <p className="upload-step-desc">Download from Moodle as a ZIP archive</p>
            </div>

            <div className="upload-step-arrow" aria-hidden="true">→</div>

            {/* Step 2 — Instructions */}
            <div className="upload-step">
              <div className="upload-step-header">
                <span className={`upload-step-number${instructionsProvided ? " upload-step-number-done" : ""}`}>{instructionsProvided ? "✅" : "2"}</span>
                <span className="upload-step-title">Assignment Instructions</span>
              </div>
              <DropZone
                label="Drop file here or click to browse"
                accept=".html,.htm,.pdf,.doc,.docx,.txt"
                file={instructionsHtml}
                onFile={(f) => { setInstructionsHtml(f); setInstructionsText(""); }}
                reusableHint={!instructionsHtml && reusableInputs?.instructions_html_name ? `Stored: ${reusableInputs.instructions_html_name}` : null}
              />
              <p className="upload-step-desc">Accepted: HTML, PDF, Word (.docx), TXT</p>
              <div className="or-divider">— or paste below —</div>
              <textarea
                rows={5}
                placeholder="Paste assignment instructions here (plain text or HTML)..."
                value={instructionsText}
                onChange={(e) => { setInstructionsText(e.target.value); if (e.target.value) setInstructionsHtml(null); }}
                disabled={Boolean(instructionsHtml)}
                className={instructionsText.trim() ? "textarea-filled" : ""}
                style={{ opacity: instructionsHtml ? 0.45 : 1 }}
              />
            </div>
          </div>

          {reusableInputs && !(mainZip && instructionsProvided) && (
            <p className="reuse-banner">
              Reusing stored inputs from job {reusableInputs.job_id}
              {reusableInputs.reused_from_job_id ? ` (copied from ${reusableInputs.reused_from_job_id})` : ""}.
              Upload new files above if you want to replace them.
            </p>
          )}

          {(loadingSubmissions || submissionOptions.length > 0) && (
            <section className="submission-picker" aria-label="Submission selection">
              <div className="submission-toolbar">
                <strong>
                  Submissions
                  {submissionOptions.length > 0 ? ` (${selectedSubmissions.length}/${submissionOptions.length} selected)` : ""}
                </strong>
                {submissionOptions.length > 0 && (
                  <div className="submission-actions">
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      onClick={() => setSelectedSubmissions(submissionOptions)}
                    >
                      Check all
                    </button>
                    <button
                      type="button"
                      className="btn-ghost btn-sm"
                      onClick={() => setSelectedSubmissions([])}
                    >
                      Uncheck all
                    </button>
                  </div>
                )}
              </div>
              {loadingSubmissions ? (
                <p className="submission-empty">Loading submissions from ZIP...</p>
              ) : submissionOptions.length === 0 ? (
                <p className="submission-empty">No submissions found to preview.</p>
              ) : (
                <div className="submission-list">
                  {submissionOptions.map((student) => {
                    const checked = selectedSubmissions.includes(student);
                    return (
                      <label key={student} className="submission-option">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setSelectedSubmissions((prev) => [...prev, student]);
                            } else {
                              setSelectedSubmissions((prev) => prev.filter((item) => item !== student));
                            }
                          }}
                        />
                        <span>{student}</span>
                      </label>
                    );
                  })}
                </div>
              )}
            </section>
          )}

          <label>
            Evaluator
            <select value={evaluatorKey} onChange={(e) => setEvaluatorKey(e.target.value)}>
              <option value="auto">auto (use routing)</option>
              {availableEvaluators.map((item) => (
                <option value={item} key={item}>{item}</option>
              ))}
            </select>
          </label>

          <fieldset className="options-grid">
            <legend>Multi-Agent Grading (Advanced)</legend>

            <label className="inline-checkbox">
              <input
                type="checkbox"
                checked={multiAgentGrading}
                onChange={(e) => setMultiAgentGrading(e.target.checked)}
              />
              Enable grader + reviewer + resolver
            </label>

            <label>
              Final score disagreement threshold
              <input
                type="number"
                min="0"
                step="0.1"
                value={disagreementThreshold}
                onChange={(e) => setDisagreementThreshold(e.target.value)}
                disabled={!multiAgentGrading}
              />
            </label>

            <label>
              Per-part disagreement threshold
              <input
                type="number"
                min="0"
                step="0.1"
                value={partDisagreementThreshold}
                onChange={(e) => setPartDisagreementThreshold(e.target.value)}
                disabled={!multiAgentGrading}
              />
            </label>
          </fieldset>

          <label>
            Instructor Notes / Grading Context (optional)
            <textarea
              rows={5}
              placeholder="Example: This week, grade only weekly progress reports. No code required. If a submission is a proposal, evaluate whether it aligns with final project requirements."
              value={gradingContext}
              onChange={(e) => setGradingContext(e.target.value)}
            />
          </label>

          <button type="submit" disabled={!canSubmit || status === "submitting"}>
            {status === "submitting" ? "Submitting..." : "Start Grading Job"}
          </button>
        </form>
      </section>
      )}

      {page === ROUTES.grader && (
      <section className="card status" ref={statusSectionRef} id="status-section">
        <div className="status-heading">
          <h2>Job Status</h2>
          {isActiveJob && <span className="pulse-dot" title="Grading in progress" />}
        </div>
        <p>
          Status: <strong>{status}</strong>
          {isActiveJob ? " — grading in progress" : ""}
        </p>
        {jobId && <p className="job-id-line">Job ID: <code>{jobId}</code></p>}
        {activeEvaluator && <p>Evaluator: <strong>{activeEvaluator}</strong></p>}
        {routeType && <p>Routed Type: <strong>{routeType}</strong></p>}
        {routingReason && <p>Routing Reason: {routingReason}</p>}
        {confidence && (
          <p>
            Confidence: <strong>{Math.round((confidence.value || 0) * 100)}%</strong>
            {confidence.source ? ` (${confidence.source})` : ""}
          </p>
        )}
        {message && <p>{message}</p>}
        {error && <p className="error">{error}</p>}

        {canStopActiveJob && (
          <button
            type="button"
            className="btn-danger"
            onClick={() => stopJob(jobId)}
            disabled={Boolean(cancelingJobIds[jobId])}
          >
            {cancelingJobIds[jobId] ? "Stopping..." : "Stop Grading"}
          </button>
        )}

        {jobId && ["stopped", "canceled", "failed"].includes(status) && (
          <button
            type="button"
            className="btn-ghost"
            onClick={() => resumeJob(jobId)}
            disabled={Boolean(resumingJobId === jobId)}
          >
            {resumingJobId === jobId ? "Resuming..." : "Resume Grading"}
          </button>
        )}

        {artifacts.filter(f => f.endsWith('.html')).length > 0 && (
          <>
            <div className="section-toolbar">
              <h3 className="section-title">Reports ({artifacts.filter(f => f.endsWith('.html')).length})</h3>
              <select
                value={reportSortMode}
                onChange={(e) => setReportSortMode(e.target.value)}
                className="sort-select"
              >
                <option value="date-desc">Latest first</option>
                <option value="date-asc">Oldest first</option>
                <option value="name-asc">Name (A-Z)</option>
                <option value="name-desc">Name (Z-A)</option>
              </select>
            </div>
            <div className="artifact-list">
              {(() => {
                let filtered = artifacts.filter(f => f.endsWith('.html'));
                if (reportSortMode === 'name-asc') {
                  filtered = filtered.sort((a, b) => a.localeCompare(b));
                } else if (reportSortMode === 'name-desc') {
                  filtered = filtered.sort((a, b) => b.localeCompare(a));
                } else if (reportSortMode === 'date-asc') {
                  filtered = filtered.reverse();
                }
                return filtered.map((file) => (
                  <div key={file} className="artifact-item">
                    <span className="artifact-name">{file}</span>
                    {file.endsWith(".html") && (
                      <button type="button" className="btn-ghost btn-sm" onClick={() => openReport(jobId, file)}>
                        View
                      </button>
                    )}
                    <a
                      href={withToken(`${API_BASE}/api/jobs/${jobId}/artifacts/${encodeURIComponent(file)}`)}
                      target="_blank"
                      rel="noreferrer"
                      className="btn-link btn-sm"
                    >
                      Download
                    </a>
                  </div>
                ));
              })()}
            </div>
          </>
        )}

        <div className="stream-header">
          <h3>Live Grading Stream</h3>
          <label className="stream-switch">
            <input
              type="checkbox"
              checked={autoScrollStream}
              onChange={(e) => setAutoScrollStream(e.target.checked)}
            />
            Auto-scroll to latest
          </label>
        </div>
        <pre className="stream-panel" ref={streamPanelRef}>
          {streamLines.length ? streamLines.join("\n") : "Waiting for grading output..."}
        </pre>
      </section>
      )}

      {page === ROUTES.history && (
      <section className="card">
        <div className="history-header">
          <h2>Job History</h2>
          <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
            {selectedJobIds.size > 0 && (
              <button
                type="button"
                className="btn-danger btn-sm"
                onClick={deleteSelectedJobs}
                disabled={bulkDeleting}
              >
                {bulkDeleting ? "Deleting..." : `Delete Selected (${selectedJobIds.size})`}
              </button>
            )}
            <button type="button" className="btn-ghost btn-sm" onClick={fetchHistory}>Refresh</button>
          </div>
        </div>
        {jobHistory.length === 0 ? (
          <p className="history-empty">No grading jobs found.</p>
        ) : (
          <table className="history-table">
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    checked={selectedJobIds.size === jobHistory.length && jobHistory.length > 0}
                    onChange={toggleSelectAll}
                    title="Select all"
                  />
                </th>
                <th>Time</th>
                <th>Status</th>
                <th>Assignment</th>
                <th>Evaluator</th>
                <th>Reports</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobHistory.map((job) => (
                <Fragment key={job.job_id}>
                  <tr className="history-row">
                    <td>
                      <input
                        type="checkbox"
                        checked={selectedJobIds.has(job.job_id)}
                        onChange={() => toggleSelectJob(job.job_id)}
                      />
                    </td>
                    <td className="history-time">{relTime(job.created_at)}</td>
                    <td><StatusBadge status={job.status} /></td>
                    <td className="history-assignment">{job.assignment_name || "—"}</td>
                    <td className="history-eval">{job.evaluator_key || "—"}</td>
                    <td>{job.artifact_count}</td>
                    <td className="history-actions">
                      {STOPPABLE_STATUSES.has(job.status) && (
                        <button
                          type="button"
                          className="btn-danger btn-sm"
                          onClick={() => stopJob(job.job_id)}
                          disabled={Boolean(cancelingJobIds[job.job_id])}
                        >
                          {cancelingJobIds[job.job_id] ? "Stopping..." : "Stop"}
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        onClick={() => openJobInGrader(job)}
                      >
                        Open in Grader
                      </button>
                      <button
                        type="button"
                        className="btn-ghost btn-sm"
                        onClick={() => toggleExpandJob(job.job_id)}
                      >
                        {expandedJobId === job.job_id ? "▲ Hide" : "▼ Reports"}
                      </button>
                      <button
                        type="button"
                        className="btn-danger btn-sm"
                        onClick={() => deleteJob(job.job_id)}
                        disabled={Boolean(deletingJobIds[job.job_id])}
                        title="Delete job"
                        aria-label="Delete job"
                      >
                        {deletingJobIds[job.job_id] ? "⏳" : "🗑️"}
                      </button>
                    </td>
                  </tr>
                  {expandedJobId === job.job_id && (
                    <tr className="history-expand-row">
                      <td colSpan={7}>
                        {!historyArtifacts[job.job_id] ? (
                          <span className="history-loading">Loading…</span>
                        ) : historyArtifacts[job.job_id].filter(f => f.endsWith('.html')).length === 0 ? (
                          <span className="history-empty-text">No reports yet.</span>
                        ) : (
                          <>
                            <div className="section-toolbar">
                              <div className="section-title section-title-sm">
                                Reports ({historyArtifacts[job.job_id].filter(f => f.endsWith('.html')).length})
                              </div>
                              <select
                                value={reportSortMode}
                                onChange={(e) => setReportSortMode(e.target.value)}
                                className="sort-select"
                              >
                                <option value="date-desc">Latest first</option>
                                <option value="date-asc">Oldest first</option>
                                <option value="name-asc">Name (A-Z)</option>
                                <option value="name-desc">Name (Z-A)</option>
                              </select>
                            </div>
                            <div className="artifact-list">
                              {(() => {
                                let filtered = historyArtifacts[job.job_id].filter(f => f.endsWith('.html'));
                                if (reportSortMode === 'name-asc') {
                                  filtered = filtered.sort((a, b) => a.localeCompare(b));
                                } else if (reportSortMode === 'name-desc') {
                                  filtered = filtered.sort((a, b) => b.localeCompare(a));
                                } else if (reportSortMode === 'date-asc') {
                                  filtered = filtered.reverse();
                                }
                                return filtered.map((f) => (
                                  <div key={f} className="artifact-item">
                                    <span className="artifact-name">{f}</span>
                                    {f.endsWith(".html") && (
                                      <button
                                        type="button"
                                        className="btn-ghost btn-sm"
                                        onClick={() => openReport(job.job_id, f)}
                                      >
                                        View
                                      </button>
                                    )}
                                    <a
                                      href={withToken(`${API_BASE}/api/jobs/${job.job_id}/artifacts/${encodeURIComponent(f)}`)}
                                      target="_blank"
                                      rel="noreferrer"
                                      className="btn-link btn-sm"
                                    >
                                      Download
                                    </a>
                                  </div>
                                ));
                              })()}
                            </div>
                          </>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </section>
      )}
    </main>
  );
}

export default App;
