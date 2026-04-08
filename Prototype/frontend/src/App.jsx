import { useEffect, useMemo, useRef, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";

function App() {
  const [mainZip, setMainZip] = useState(null);
  const [instructionsHtml, setInstructionsHtml] = useState(null);
  const [jobId, setJobId] = useState("");
  const [status, setStatus] = useState("idle");
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
  const streamPanelRef = useRef(null);

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

  const canSubmit = useMemo(() => Boolean(mainZip && instructionsHtml), [mainZip, instructionsHtml]);

  useEffect(() => {
    if (!jobId || status === "finished" || status === "failed" || streamConnected) {
      return;
    }

    const timer = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/jobs/${jobId}`);
        if (!res.ok) {
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
  }, [jobId, status, streamConnected]);

  useEffect(() => {
    if (!jobId) {
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
        if (data.status === "finished" || data.status === "failed") {
          stream.close();
        }
      } catch {
        // Ignore malformed stream packets.
      }
    });

    stream.onerror = () => {
      setStreamConnected(false);
      // Do not close manually; EventSource will auto-reconnect.
      setError((prev) => prev || "Live stream disconnected temporarily. Retrying...");
    };

    return () => {
      setStreamConnected(false);
      stream.close();
    };
  }, [jobId]);

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
    form.append("main_zip", mainZip);
    form.append("instructions_html", instructionsHtml);
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
    } catch (err) {
      setStatus("failed");
      setError(err.message || "Unknown submit error");
    }
  }

  return (
    <main className="page">
      <section className="hero">
        <h1>AutoGrade Control Panel</h1>
        <p>Upload submissions and assignment instructions, then track grading in real time.</p>
      </section>

      <section className="card">
        <form onSubmit={submitJob} className="form">
          <label>
            Main submissions ZIP
            <input type="file" accept=".zip" onChange={(e) => setMainZip(e.target.files?.[0] || null)} />
          </label>

          <label>
            Assignment instructions HTML
            <input type="file" accept=".html,text/html" onChange={(e) => setInstructionsHtml(e.target.files?.[0] || null)} />
          </label>

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

      <section className="card status">
        <h2>Job Status</h2>
        <p>Status: <strong>{status}</strong></p>
        {jobId && <p>Job ID: {jobId}</p>}
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

        {artifacts.length > 0 && (
          <>
            <h3>Artifacts</h3>
            <ul>
              {artifacts.map((file) => (
                <li key={file}>
                  <a href={`${API_BASE}/api/jobs/${jobId}/artifacts/${encodeURIComponent(file)}`} target="_blank" rel="noreferrer">
                    {file}
                  </a>
                </li>
              ))}
            </ul>
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
    </main>
  );
}

export default App;
