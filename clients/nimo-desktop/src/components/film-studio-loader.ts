import { lazy } from "react";

const loadFilmStudioPage = () =>
  import("./FilmStudioPage").then((module) => ({ default: module.FilmStudioPage }));

/** Keep the storyboard canvas and timeline out of the application-shell chunk. */
export const LazyFilmStudioPage = lazy(loadFilmStudioPage);

export function preloadFilmStudioPage(): Promise<unknown> {
  return loadFilmStudioPage();
}
