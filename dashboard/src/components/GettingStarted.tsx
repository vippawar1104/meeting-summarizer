import { Icon } from "./Icon";

interface Props {
  hasReviews: boolean;
  usesOwnKey: boolean;
  installUrl: string | null;
}

/** Shown until the first review lands: what to do next, in plain steps. */
export function GettingStarted({ hasReviews, usesOwnKey, installUrl }: Props) {
  const steps = [
    { done: true, title: "Reviewly is installed", body: "It watches every pull request in the repositories you chose." },
    {
      done: hasReviews,
      title: "Open a pull request",
      body: "That's it. Reviewly reads the diff and posts one review with inline comments, usually within a minute.",
    },
    {
      done: usesOwnKey,
      title: "Optional: use your own AI key",
      body: "Prefer a specific model? Add your provider key under AI model below.",
    },
  ];
  return (
    <section aria-label="Getting started">
      <h2>
        <Icon name="rocket" size={14} />
        Getting started
      </h2>
      <ol className="steps card">
        {steps.map((s) => (
          <li key={s.title} className={s.done ? "done" : ""}>
            <span className="step-icon">
              <Icon name={s.done ? "check-circle" : "dot"} size={16} />
            </span>
            <div>
              <div className="step-title">{s.title}</div>
              <div className="muted">{s.body}</div>
            </div>
          </li>
        ))}
      </ol>
      {installUrl && (
        <p className="sub" style={{ marginTop: 10 }}>
          Want more repositories?{" "}
          <a className="inline-link" href={installUrl}>
            Add them on GitHub <Icon name="external-link" size={11} />
          </a>
        </p>
      )}
    </section>
  );
}
