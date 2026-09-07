import { lazy } from "react";

const loadWorkflowPage = () =>
  import("./WorkflowPage").then((module) => ({
    default: module.WorkflowPage,
  }));

export const LazyWorkflowPage = lazy(loadWorkflowPage);

export function preloadWorkflowPage(): Promise<unknown> {
  return loadWorkflowPage();
}
