import type { ProjectView } from "@nimo/engine-contracts";

/**
 * 按持久化顺序重排卷册（mirrors dashboard_page.py bookshelf 排序语义）。
 * 未知 id 一律保留相对顺序追加到末尾。
 */
export function orderProjects(
  projects: readonly ProjectView[],
  preferredOrder: readonly string[],
): readonly ProjectView[] {
  if (preferredOrder.length === 0) return projects;
  const projectsById = new Map(projects.map((project) => [project.id, project]));
  const ordered: ProjectView[] = [];
  const seen = new Set<string>();
  for (const projectId of preferredOrder) {
    const project = projectsById.get(projectId);
    if (project === undefined || seen.has(projectId)) continue;
    ordered.push(project);
    seen.add(projectId);
  }
  for (const project of projects) {
    if (seen.has(project.id)) continue;
    ordered.push(project);
  }
  return ordered;
}
