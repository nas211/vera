import { createContext, useContext } from 'react';

/** A presentation/rehearsal boundary; it never enables account services. */
export const PreviewMode = createContext(false);
export const usePreviewMode = () => useContext(PreviewMode);
