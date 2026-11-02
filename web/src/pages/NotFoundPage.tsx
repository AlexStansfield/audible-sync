/**
 * 404 inside the layout (NotFound.dc.html): an unknown route, or a book the API does
 * not have (`kind="book"`, which Book detail renders on a 404).
 */
import { Link, useLocation } from "react-router";
import styles from "./NotFoundPage.module.css";

const TEXT = {
  route: {
    title: "Page not found",
    text: "There is nothing at this address. Check the link, or start again from the dashboard.",
    primary: "Open the library",
  },
  book: {
    title: "This book is not in your library",
    text: "It may belong to an account that was removed, or the link is out of date.",
    primary: "Back to the library",
  },
};

export function NotFoundPage({ kind = "route" }: { kind?: "route" | "book" }) {
  const location = useLocation();
  const text = TEXT[kind];
  return (
    <div className={styles.page}>
      <div className={styles.box}>
        <span aria-hidden="true" className={styles.code}>
          404
        </span>
        <h1 className={styles.title}>{text.title}</h1>
        <p className={styles.text}>{text.text}</p>
        <code className={styles.path}>{location.pathname}</code>
        <div className={styles.actions}>
          <Link to="/library" className="btn btn-primary">
            {text.primary}
          </Link>
          <Link to="/" className="btn btn-secondary">
            Go to the dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
