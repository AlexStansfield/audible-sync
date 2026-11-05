import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fail, fakeApi, ME, ok, signedIn } from "../test/fakeApi";
import { renderApp } from "../test/render";

function form(name: string) {
  return within(screen.getByRole("form", { name }));
}

describe("Your sign-in", () => {
  it("asks you to choose your own password while the generated one is in use", async () => {
    fakeApi(signedIn({ "GET /api/auth/me": ok({ ...ME, generated_password: true }) }));
    renderApp("/profile");

    expect(await screen.findByText("You are using the generated password")).toBeInTheDocument();
  });

  it("changes the username", async () => {
    const api = fakeApi(signedIn({ "PUT /api/auth/username": ok({ ...ME, username: "alex" }) }));
    renderApp("/profile");
    await screen.findByText("admin", { selector: "strong" });

    await userEvent.type(form("Username").getByLabelText("New username"), " alex ");
    await userEvent.type(form("Username").getByLabelText("Current password"), "the old one");
    await userEvent.click(screen.getByRole("button", { name: "Change username" }));

    expect(await screen.findByText("Username changed. Use it next time you sign in.")).toBeInTheDocument();
    expect(api.sent("PUT /api/auth/username")).toEqual([{ username: "alex", current_password: "the old one" }]);
    expect(screen.getByText("alex", { selector: "strong" })).toBeInTheDocument();
  });

  it("puts a wrong current password on its field", async () => {
    fakeApi(signedIn({ "PUT /api/auth/username": fail(403, "The current password is wrong") }));
    renderApp("/profile");
    await screen.findByRole("form", { name: "Username" });

    await userEvent.type(form("Username").getByLabelText("New username"), "alex");
    await userEvent.type(form("Username").getByLabelText("Current password"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Change username" }));

    const field = form("Username").getByLabelText("Current password");
    expect(await form("Username").findByText("The current password is wrong.")).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");
  });

  it("shows a mismatch under the confirmation and sends nothing", async () => {
    const api = fakeApi(signedIn());
    renderApp("/profile");
    const password = within(await screen.findByRole("form", { name: "Password" }));

    await userEvent.type(password.getByLabelText("Current password"), "the old one");
    await userEvent.type(password.getByLabelText("New password"), "correct horse battery");
    await userEvent.type(password.getByLabelText("Confirm new password"), "correct horse batery");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(password.getByText("The two passwords do not match.")).toBeInTheDocument();
    expect(api.sent("PUT /api/auth/password")).toEqual([]);
  });

  it("refuses a short password before sending it", async () => {
    const api = fakeApi(signedIn());
    renderApp("/profile");
    const password = within(await screen.findByRole("form", { name: "Password" }));

    await userEvent.type(password.getByLabelText("Current password"), "the old one");
    await userEvent.type(password.getByLabelText("New password"), "short");
    await userEvent.type(password.getByLabelText("Confirm new password"), "short");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(password.getByLabelText("New password")).toHaveAttribute("aria-invalid", "true");
    expect(api.sent("PUT /api/auth/password")).toEqual([]);
  });

  it("changes the password and says the other browsers are signed out", async () => {
    const api = fakeApi(
      signedIn({
        "GET /api/auth/me": ok({ ...ME, generated_password: true }),
        "PUT /api/auth/password": ok(ME),
      }),
    );
    renderApp("/profile");
    const password = within(await screen.findByRole("form", { name: "Password" }));

    await userEvent.type(password.getByLabelText("Current password"), "the old one");
    await userEvent.type(password.getByLabelText("New password"), "correct horse battery");
    await userEvent.type(password.getByLabelText("Confirm new password"), "correct horse battery");
    await userEvent.click(screen.getByRole("button", { name: "Change password" }));

    expect(await screen.findByText(/Password changed. Other browsers are signed out/)).toBeInTheDocument();
    expect(api.sent("PUT /api/auth/password")).toEqual([
      { current_password: "the old one", new_password: "correct horse battery" },
    ]);
    expect(screen.queryByText("You are using the generated password")).not.toBeInTheDocument();
    expect(password.getByLabelText("New password")).toHaveValue("");
  });
});
