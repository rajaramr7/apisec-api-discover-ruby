class WebhooksController < ApplicationController
  skip_before_action :authenticate_user!

  def stripe; end
end
