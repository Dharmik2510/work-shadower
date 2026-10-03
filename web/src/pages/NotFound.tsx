import { Link } from "react-router-dom";
import { Dot } from "../components/Dot";
import { Empty } from "../components/bits";

export default function NotFound() {
  return (
    <div className="page">
      <Empty art={<Dot size={56} />} title="There's nothing at this address" actions={<Link className="btn btn--quiet" to="/">Go to the library</Link>}>
        <p>The link may be mistyped, or the skill was archived.</p>
      </Empty>
    </div>
  );
}
