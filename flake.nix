{
  description = "A terminal coding agent for self-hosted models";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      # No flake-utils: one input fewer to keep current, and the whole
      # of what it would provide here is this list and a fold over it.
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system:
          f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        quaso = pkgs.python3Packages.buildPythonApplication {
          pname = "quaso";
          version = "0.1.0";
          pyproject = true;
          src = ./.;

          build-system = [ pkgs.python3Packages.hatchling ];

          dependencies = with pkgs.python3Packages; [
            httpx
            pydantic
            rich
            prompt-toolkit
          ];

          nativeCheckInputs = with pkgs.python3Packages; [
            pytestCheckHook
            pytest-asyncio
            respx
          ];

          # The escape tests need a sandbox to exercise, and the Nix
          # build is itself sandboxed: bwrap cannot nest, and Seatbelt
          # is not reachable from a derivation. They run in CI instead,
          # where there is a real machine underneath.
          disabledTestPaths = [ "tests/test_escapes.py" ];

          meta = with pkgs.lib; {
            description = "A terminal coding agent for self-hosted models";
            homepage = "https://github.com/jacenkow/quaso";
            license = licenses.mit;
            mainProgram = "quaso";
            platforms = platforms.unix;
          };
        };
        default = quaso;
      });

      apps = forAllSystems (pkgs: rec {
        quaso = {
          type = "app";
          program = "${self.packages.${pkgs.system}.quaso}/bin/quaso";
        };
        default = quaso;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [
              httpx
              pydantic
              rich
              prompt-toolkit
              pytest
              pytest-asyncio
              respx
            ]))
            pkgs.ruff
          ]
          # What the sandbox needs on Linux. Absent on Darwin, where
          # Seatbelt is part of the system already.
          ++ pkgs.lib.optional pkgs.stdenv.isLinux pkgs.bubblewrap;
        };
      });
    };
}
