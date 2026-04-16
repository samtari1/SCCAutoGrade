import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

const RUNNING_STATUSES = new Set(["queued", "started", "deferred", "scheduled", "submitting", "pending"]);
const STOPPABLE_STATUSES = new Set(["queued", "started", "deferred", "scheduled", "pending"]);
const ROUTES = {
  grader: "grader",
  history: "history"
};

function routeFromHash() {
  return window.location.hash === "#/history" ? ROUTES.history : ROUTES.grader;
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

function App() {
  const [page, setPage] = useState(routeFromHash);
  const [mainZip, setMainZip] = useState(null);
  const [instructionsHtml, setInstructionsHtml] = useState(null);
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

  useEffect(() => {
    const onHashChange = () => setPage(routeFromHash());
    window.addEventListener("hashchange", onHashChange);
    onHashChange();
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  function navigateTo(nextPage) {
    const nextHash = nextPage === ROUTES.history ? "#/history" : "#/";
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
    setPage(nextPage);
  }

  useEffect(() => {
    const loadEvaluators = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/evaluators`);
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
  }, []);

  // Persist current job ID across page refreshes.
  useEffect(() => {
    if (jobId) localStorage.setItem("ag_job_id", jobId);
  }, [jobId]);

  useEffect(() => {
    if (!jobId) {
      setReusableInputs(null);
      return;
    }

    let isCancelled = false;
    const loadReusableInputs = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/jobs/${jobId}/input-info`);
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
  }, [jobId, status]);

  useEffect(() => {
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

        const res = await fetch(`${API_BASE}/api/submissions/preview`, {
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
  }, [mainZip, reusableInputs]);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/jobs`);
      if (!res.ok) return [];
      const data = await res.json();
      const jobs = data.jobs || [];
      setJobHistory(jobs);
      return jobs;
    } catch {
      // history panel is non-critical
      return [];
    }
  }, []);

  const syncCurrentJobFromHistory = useCallback(async (targetJobId) => {
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
        const res = await fetch(`${API_BASE}/api/jobs/${currentId}/artifacts`);
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
  }, [fetchHistory, jobId]);

  useEffect(() => {
    if (page !== ROUTES.history) {
      return;
    }

    fetchHistory();
    const timer = setInterval(fetchHistory, 10000);
    return () => clearInterval(timer);
  }, [fetchHistory, page]);

  const canSubmit = useMemo(
    () => Boolean(
      ((mainZip && instructionsHtml) || reusableInputs) &&
      (submissionOptions.length === 0 || selectedSubmissions.length > 0)
    ),
    [mainZip, instructionsHtml, reusableInputs, submissionOptions.length, selectedSubmissions.length]
  );

  useEffect(() => {
    if (!jobId || ["finished", "failed", "stopped", "canceled"].includes(status) || streamConnected) {
      return;
    }

    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/jobs/${jobId}`);
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
  }, [jobId, status, streamConnected, syncCurrentJobFromHistory]);

  useEffect(() => {
    if (page !== ROUTES.grader || !jobId) {
      return;
    }
    if (!["finished", "failed", "stopped", "canceled"].includes(status)) {
      return;
    }

    let isCancelled = false;
    const loadArtifactsForTerminalJob = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/jobs/${jobId}/artifacts`);
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
  }, [page, jobId, status]);

  useEffect(() => {
    if (!jobId || ["finished", "failed", "stopped", "canceled"].includes(status)) {
      setStreamConnected(false);
      return;
    }

    const stream = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream`);

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
  }, [jobId, status, syncCurrentJobFromHistory]);

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
    if (mainZip && instructionsHtml) {
      form.append("main_zip", mainZip);
      form.append("instructions_html", instructionsHtml);
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
      const res = await fetch(`${API_BASE}/api/jobs`, {
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
        const res = await fetch(`${API_BASE}/api/jobs/${jid}/artifacts`);
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
    setReportUrl(`${API_BASE}/api/jobs/${jid}/artifacts/${encodeURIComponent(filename)}`);
    setReportTitle(filename);
  }

  async function stopJob(targetJobId) {
    if (!targetJobId || cancelingJobIds[targetJobId]) {
      return;
    }

    setCancelingJobIds((prev) => ({ ...prev, [targetJobId]: true }));
    try {
      const res = await fetch(`${API_BASE}/api/jobs/${targetJobId}/cancel`, {
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

  async function resumeJob(targetJobId) {
    if (!targetJobId || resumingJobId) {
      return;
    }

    setResumingJobId(targetJobId);
    try {
      const res = await fetch(`${API_BASE}/api/jobs/${targetJobId}/resume`, {
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
      const res = await fetch(`${API_BASE}/api/jobs/${job.job_id}/artifacts`);
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
      const res = await fetch(`${API_BASE}/api/submissions/preview`, {
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

  const isActiveJob = RUNNING_STATUSES.has(status);
  const canStopActiveJob = Boolean(jobId) && STOPPABLE_STATUSES.has(status);

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
        <h1>AutoGrade Control Panel</h1>
        <p>
          {page === ROUTES.grader
            ? "Upload submissions and assignment instructions, then track grading in real time."
            : "Browse all past grading batches, monitor running jobs, and open reports."}
        </p>
      </section>

      <section className="card nav-card">
        <div className="top-nav" role="tablist" aria-label="Main pages">
          <button
            type="button"
            className={page === ROUTES.grader ? "btn-ghost nav-btn nav-btn-active" : "btn-ghost nav-btn"}
            onClick={() => navigateTo(ROUTES.grader)}
            aria-current={page === ROUTES.grader ? "page" : undefined}
          >
            Grading
          </button>
          <button
            type="button"
            className={page === ROUTES.history ? "btn-ghost nav-btn nav-btn-active" : "btn-ghost nav-btn"}
            onClick={() => navigateTo(ROUTES.history)}
            aria-current={page === ROUTES.history ? "page" : undefined}
          >
            History
          </button>
        </div>
      </section>

      {page === ROUTES.grader && (
      <section className="card">
        <form onSubmit={submitJob} className="form">
          <label>
            Main submissions ZIP
            <input type="file" accept=".zip" onChange={(e) => setMainZip(e.target.files?.[0] || null)} />
            {!mainZip && reusableInputs?.main_zip_name && (
              <span className="reuse-hint">Stored from unfinished job: {reusableInputs.main_zip_name}</span>
            )}
          </label>

          <label>
            Assignment instructions HTML
            <input type="file" accept=".html,text/html" onChange={(e) => setInstructionsHtml(e.target.files?.[0] || null)} />
            {!instructionsHtml && reusableInputs?.instructions_html_name && (
              <span className="reuse-hint">Stored from unfinished job: {reusableInputs.instructions_html_name}</span>
            )}
          </label>

          {reusableInputs && !(mainZip && instructionsHtml) && (
            <p className="reuse-banner">
              Reusing stored inputs from job {reusableInputs.job_id}
              {reusableInputs.reused_from_job_id ? ` (copied from ${reusableInputs.reused_from_job_id})` : ""}.
              Select new files above if you want to replace them.
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
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.75rem' }}>
              <h3 style={{ margin: 0 }}>Reports ({artifacts.filter(f => f.endsWith('.html')).length})</h3>
              <select
                value={reportSortMode}
                onChange={(e) => setReportSortMode(e.target.value)}
                style={{
                  padding: '0.35rem 0.5rem',
                  fontSize: '0.85rem',
                  borderRadius: '6px',
                  border: '1px solid var(--line)',
                  background: '#fff',
                  fontFamily: 'inherit',
                  cursor: 'pointer',
                }}
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
                      href={`${API_BASE}/api/jobs/${jobId}/artifacts/${encodeURIComponent(file)}`}
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
          <button type="button" className="btn-ghost btn-sm" onClick={fetchHistory}>Refresh</button>
        </div>
        {jobHistory.length === 0 ? (
          <p className="history-empty">No grading jobs found.</p>
        ) : (
          <table className="history-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Status</th>
                <th>Evaluator</th>
                <th>Reports</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {jobHistory.map((job) => (
                <Fragment key={job.job_id}>
                  <tr className="history-row">
                    <td className="history-time">{relTime(job.created_at)}</td>
                    <td><StatusBadge status={job.status} /></td>
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
                    </td>
                  </tr>
                  {expandedJobId === job.job_id && (
                    <tr className="history-expand-row">
                      <td colSpan={5}>
                        {!historyArtifacts[job.job_id] ? (
                          <span className="history-loading">Loading…</span>
                        ) : historyArtifacts[job.job_id].filter(f => f.endsWith('.html')).length === 0 ? (
                          <span className="history-empty-text">No reports yet.</span>
                        ) : (
                          <>
                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', marginBottom: '0.75rem' }}>
                              <div style={{ fontSize: '0.9rem', fontWeight: '600' }}>
                                Reports ({historyArtifacts[job.job_id].filter(f => f.endsWith('.html')).length})
                              </div>
                              <select
                                value={reportSortMode}
                                onChange={(e) => setReportSortMode(e.target.value)}
                                style={{
                                  padding: '0.35rem 0.5rem',
                                  fontSize: '0.85rem',
                                  borderRadius: '6px',
                                  border: '1px solid var(--line)',
                                  background: '#fff',
                                  fontFamily: 'inherit',
                                  cursor: 'pointer',
                                }}
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
                                      href={`${API_BASE}/api/jobs/${job.job_id}/artifacts/${encodeURIComponent(f)}`}
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
