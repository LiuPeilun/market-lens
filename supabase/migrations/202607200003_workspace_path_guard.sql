alter table public.workspace_files
add constraint workspace_files_safe_path_chars
check (path !~ '[[:cntrl:],()''"]');
