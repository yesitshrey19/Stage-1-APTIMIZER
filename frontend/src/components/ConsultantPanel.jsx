import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Sparkles } from "lucide-react";
import { api, apiError } from "../lib/api";
import { Section } from "./Field";
import { Markdown } from "./Markdown";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "./ui/select";
import { dt } from "../lib/format";

// One topic, one structured memo -- the consultant answers a question the engineer
// already has, from figures the site analysis has already computed. Not a chat: no
// history, no follow-ups. A memo generated earlier for a topic is shown again.
export default function ConsultantPanel({
  projectId,
  saved = null,           // project.ai.consult -- memos stored per topic
  defaultTopic = "general",
  readOnly = false,
  aiReady = true,
  testid = "ai-consult",
}) {
  const [topics, setTopics] = useState([]);
  const [topic, setTopic] = useState(defaultTopic);
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [memos, setMemos] = useState(saved || {});

  useEffect(() => {
    api.get("/ai/consult/topics")
      .then(({ data }) => {
        setTopics(data);
        if (data.length && !data.some((t) => t.id === topic)) setTopic(data[0].id);
      })
      .catch(() => setTopics([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => { setMemos(saved || {}); }, [saved]);

  const memo = memos[topic];

  const run = async () => {
    setBusy(true);
    try {
      const { data } = await api.post(`/projects/${projectId}/ai/consult`, {
        topic, question: question.trim(),
      });
      setMemos((m) => ({ ...m, [topic]: data }));
      toast.success("Advisory memo generated");
    } catch (e) {
      toast.error(apiError(e.response?.data?.detail));
    } finally {
      setBusy(false);
    }
  };

  const disabled = busy || readOnly || !aiReady;

  return (
    <Section
      title="AI Engineering Consultant"
      description="Ask about flood, wind, earthquake or the site as a whole — an advisory memo grounded in this site analysis"
      testid={`${testid}-section`}
    >
      <div className="flex flex-col sm:flex-row gap-2">
        <Select value={topic} onValueChange={setTopic} disabled={busy || readOnly}>
          <SelectTrigger className="w-full sm:w-56 rounded-sm h-9" data-testid={`${testid}-topic`}>
            <SelectValue placeholder="Topic" />
          </SelectTrigger>
          <SelectContent>
            {topics.map((t) => (
              <SelectItem key={t.id} value={t.id}>{t.label}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Optional — e.g. how high should the plinth be on this site?"
          className="flex-1 rounded-sm h-9"
          disabled={disabled}
          data-testid={`${testid}-question`}
          onKeyDown={(e) => { if (e.key === "Enter" && !disabled) run(); }}
        />
        <Button onClick={run} disabled={disabled} variant="ai" className="rounded-sm h-9"
          data-testid={`${testid}-run`}>
          <Sparkles className={`h-3.5 w-3.5 mr-1.5 ${busy ? "animate-pulse" : ""}`} />
          {busy ? "Consulting…" : memo ? "Re-consult" : "Consult"}
        </Button>
      </div>
      {memo ? (
        <div className="mt-3 border rounded-sm px-3 py-2" data-testid={`${testid}-memo`}>
          <Markdown text={memo.text} testid={`${testid}-memo-text`} />
          <p className="text-[11px] text-slate-400 font-mono mt-2">
            {memo.model} · {dt(memo.generated_at)}
          </p>
        </div>
      ) : !aiReady ? (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-sm px-3 py-2 mt-2">
          AI is not configured on the server.
        </p>
      ) : (
        <p className="text-sm text-slate-500 mt-2">
          The memo cites only numbers the site analysis produced — where data is missing it says so
          instead of guessing.
        </p>
      )}
    </Section>
  );
}
