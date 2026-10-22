"""Gerçek model E2E testi için küçük PDF üretir; depoda PDF saklamaz.

GPU/Docling test ortamında: python scripts/create_formula_test_pdf.py --output /tmp/formulas.pdf
"""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Çıktı zaten var; üzerine yazılmaz. Başka bir dosya yolu seçin.")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(8.27, 11.69))
    figure.text(.10, .92, "Probability and algebra", fontsize=20)
    figure.text(.10, .86, "Conditional probability and Bayes theorem", fontsize=16)
    figure.text(.10, .80, "Bayes theorem relates conditional probabilities of events A and B.\nP(A) is the prior probability. P(B) is the evidence probability.\nP(B|A) is the likelihood. P(A|B) is the posterior probability.", fontsize=11, linespacing=1.8)
    figure.text(.15, .65, r"$P(A\mid B)=\frac{P(B\mid A)P(A)}{P(B)}$", fontsize=24)
    figure.text(.10, .52, "Quadratic equations", fontsize=16)
    figure.text(.10, .46, "A quadratic equation has degree two. The discriminant b squared\nminus four times a times c determines whether its roots are real.\nThe plus-minus sign represents the two possible roots.", fontsize=11, linespacing=1.8)
    figure.text(.15, .30, r"$x=\frac{-b\pm\sqrt{b^2-4ac}}{2a}$", fontsize=24)
    figure.savefig(args.output)
    plt.close(figure)
    print(args.output)


if __name__ == "__main__":
    main()
