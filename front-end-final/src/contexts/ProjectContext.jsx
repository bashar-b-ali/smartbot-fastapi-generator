import React, { createContext, useContext, useState, useCallback } from 'react';
import { projectService } from '../services/api';

const ProjectContext = createContext(null);

export const useProject = () => {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error('useProject must be used within a ProjectProvider');
  return ctx;
};

export const ProjectProvider = ({ children }) => {
  const [projects, setProjects] = useState([]);
  const [loading, setLoading] = useState(false);

  const fetchProjects = useCallback(async () => {
    setLoading(true);
    try {
      const data = await projectService.list();
      const arr = Array.isArray(data) ? data : [];
      setProjects(arr);
      return arr;
    } catch {
      setProjects([]);
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  const getProject = useCallback((id) => projectService.get(id), []);

  const createProject = useCallback(async (data) => {
    setLoading(true);
    try {
      const response = await projectService.create(data);
      const project = response?.project || response;
      setProjects((prev) => [...(Array.isArray(prev) ? prev : []), project]);
      return { success: true, project };
    } catch (err) {
      return {
        success: false,
        error: err?.message || 'Failed to create project. Please try again.',
      };
    } finally {
      setLoading(false);
    }
  }, []);

  const deleteProject = useCallback(async (id) => {
    try {
      await projectService.delete(id);
      setProjects((prev) =>
        (Array.isArray(prev) ? prev : []).filter((p) => p.id !== id)
      );
      return { success: true };
    } catch (err) {
      return { success: false, error: err?.message || 'Failed to delete project' };
    }
  }, []);

  const updateProject = useCallback(async (id, data) => {
    try {
      const response = await projectService.update(id, data);
      const project = response?.project || response;
      setProjects((prev) =>
        (Array.isArray(prev) ? prev : []).map((p) => (p.id === id ? project : p))
      );
      return { success: true, project };
    } catch (err) {
      return { success: false, error: err?.message || 'Failed to update project' };
    }
  }, []);

  const getFolderContent = useCallback(
    (id, path = '') => projectService.getFolderContent(id, path),
    []
  );

  const value = {
    projects,
    loading,
    fetchProjects,
    getProject,
    createProject,
    deleteProject,
    updateProject,
    getFolderContent,
  };

  return <ProjectContext.Provider value={value}>{children}</ProjectContext.Provider>;
};
