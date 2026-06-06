"""Train MLB run line margin model and write artifact."""

from app.models.mlb_spread import run_training


def main() -> None:
    results = run_training()
    print("Spread model trained.")
    print(f"  Holdout MAE (margin): {results['holdout_mae_margin']}")
    print(f"  Margin std: {results['holdout_margin_std']}")
    print(f"  Proxy ±1.5 home cover acc: {results['proxy_cover_accuracy_home']}")


if __name__ == "__main__":
    main()
