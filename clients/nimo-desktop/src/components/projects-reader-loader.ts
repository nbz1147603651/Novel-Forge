import { lazy } from "react";

const loadProjectsReader = () =>
  import("./ProjectsReader").then((module) => ({
    default: module.ProjectsReader,
  }));

export const LazyProjectsReader = lazy(loadProjectsReader);

export function preloadProjectsReader(): Promise<unknown> {
  return loadProjectsReader();
}
