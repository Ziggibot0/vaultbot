# how to safe_write

## Summary
Autonomous research into 'how to safe_write' to fill a procedural_gap gap. 1 sources, 6 corroborated facts.

## Key Findings
- ghc-internal-9.1401.0: Basic libraries Source Contents Index Copyright (c) The University of Glasgow 1992-2002 License see libraries/base/LICENSE Maintainer ghc-devs@haskell.org Stability internal Portability non-portable (requires POSIX) Safe Haskell Trustworthy Language Haskell2010 GHC.Internal.System.Posix.Internals Description POSIX support layer for the standard libraries.  [sources: c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize]
- The API of this module is unstable and not meant to be consumed by the general public.  [sources: c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize]
- If you absolutely must depend on it, make sure to use a tight upper bound, e.g., base < 4.X rather than base < 5 , because the interface can change rapidly without much warning.  [sources: c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize]
- This library is built on *every* platform, including Win32.  [sources: c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize]
- We want to be able to interrupt an openFile call if it's expensive (NFS, FUSE, etc.), and we especially need to be able to interrupt a blocking open call.  [sources: c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize]
- Since: base-4.16.0.0 c_safe_open :: CFilePath -> CInt -> CMode -> IO CInt Source # hostIsThreaded :: Bool Source # Consult the RTS to find whether it is threaded.  [sources: c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize]

## Sources
- [c_safe_write :: CInt -> Ptr Word8 -> CSize -> IO CSsize](https://hackage.haskell.org/package/ghc-internal/docs/GHC-Internal-System-Posix-Internals.html#v:c_safe_write) ([[learningMaterial/web/hackage-haskell-org-package-ghc-internal-docs-ghc-internal-system-posix-0bc08b0a.html|archived]])

## Follow-up Queries (gap fill)
- safe_write definition means
- safe_write example such as
- safe_write safe_write

<!-- research: 1 sources, 6 facts, 2 rounds -->