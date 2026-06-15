import { Navigate, useParams } from "react-router-dom";

export default function DealDetail() {
  const { id } = useParams<{ id: string }>();

  if (!id) {
    return <Navigate to="/pipeline" replace />;
  }

  return <Navigate to={`/pipeline?deal=${id}`} replace />;
}
